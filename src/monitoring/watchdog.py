"""
Triada Watchdog v1.0
====================

Standalone external supervisor for the `hydra-bot` container.

Runs as an isolated Docker service (`triada-watchdog`) so it stays alive even
if the main bot's Python interpreter fully deadlocks (e.g. C-extension hang).

Decision loop:
  1. Every POLL_INTERVAL_SEC seconds, scrape Prometheus HTTP API for:
       - hydra_health_status      (Gauge: 1=ok, 0=degraded)
       - hydra_scan_cycles_total  (Counter, monotonically increasing)
  2. Trigger a forced restart of `hydra-bot` when EITHER:
       a) hydra_health_status has been == 0 for >= STALL_THRESHOLD_SEC, OR
       b) hydra_scan_cycles_total has not increased for >= STALL_THRESHOLD_SEC
          (i.e. main loop frozen / locked / deadlocked).
  3. After a restart, internal state is reset and a cooldown of
     RESTART_COOLDOWN_SEC seconds is enforced before another decision is made.

Telemetry / external messengers are intentionally OUT OF SCOPE — the system
must achieve absolute silent self-healing.

Environment variables (optional, sane defaults provided):
  PROMETHEUS_URL          default: http://prometheus:9090
  TARGET_CONTAINER        default: hydra-bot
  POLL_INTERVAL_SEC       default: 5
  STALL_THRESHOLD_SEC     default: 25
  RESTART_COOLDOWN_SEC    default: 60
  RESTART_TIMEOUT_SEC     default: 2
"""

from __future__ import annotations

import logging
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Optional, Protocol

import requests

logger = logging.getLogger("triada.watchdog")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class WatchdogConfig:
    prometheus_url: str = os.getenv("PROMETHEUS_URL", "http://prometheus:9090")
    target_container: str = os.getenv("TARGET_CONTAINER", "hydra-bot")
    poll_interval_sec: float = float(os.getenv("POLL_INTERVAL_SEC", "5"))
    stall_threshold_sec: float = float(os.getenv("STALL_THRESHOLD_SEC", "25"))
    restart_cooldown_sec: float = float(os.getenv("RESTART_COOLDOWN_SEC", "60"))
    restart_timeout_sec: int = int(os.getenv("RESTART_TIMEOUT_SEC", "2"))
    request_timeout_sec: float = float(os.getenv("PROM_REQUEST_TIMEOUT_SEC", "3"))
    # Grace period after watchdog start AND after each restart, during which
    # NO restart can be issued. Protects against false freezes while the bot
    # is starting up (e.g. WS connect, model load, Prometheus scrape interval).
    startup_grace_sec: float = float(os.getenv("STARTUP_GRACE_SEC", "60"))


# ---------------------------------------------------------------------------
# Pluggable interfaces (so tests can inject fakes without docker / network)
# ---------------------------------------------------------------------------

class PrometheusClient:
    """Minimal Prometheus HTTP API client — fetches latest sample of a metric."""

    def __init__(self, base_url: str, request_timeout_sec: float = 3.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = request_timeout_sec

    def query_scalar(self, metric: str) -> Optional[float]:
        """Latest scalar value for `metric` via instant query, or None on error."""
        url = f"{self.base_url}/api/v1/query"
        try:
            r = requests.get(url, params={"query": metric}, timeout=self.timeout)
            r.raise_for_status()
            payload = r.json()
        except Exception as e:
            logger.warning("Prometheus query failed for %s: %s", metric, e)
            return None
        if payload.get("status") != "success":
            logger.warning("Prometheus non-success for %s: %s", metric, payload)
            return None
        result = payload.get("data", {}).get("result", [])
        if not result:
            return None
        try:
            return float(result[0]["value"][1])
        except (KeyError, IndexError, TypeError, ValueError) as e:
            logger.warning("Bad Prometheus payload for %s: %s", metric, e)
            return None


class ContainerController(Protocol):
    """Abstraction over the docker SDK so tests can inject a fake."""

    def restart(self, name: str, timeout: int) -> None:  # pragma: no cover
        ...


class DockerContainerController:
    """Real docker controller (uses the `docker` SDK over the mounted socket)."""

    def __init__(self):
        import docker  # local import: not needed in tests
        self._client = docker.from_env()

    def restart(self, name: str, timeout: int) -> None:
        container = self._client.containers.get(name)
        container.restart(timeout=timeout)


# ---------------------------------------------------------------------------
# Watchdog state machine
# ---------------------------------------------------------------------------

@dataclass
class _State:
    health_bad_since: Optional[float] = None
    last_heartbeat_ts: Optional[float] = None
    last_restart_ts: float = 0.0
    # Timestamp at which the current observation window began (watchdog start
    # or last restart). Used to enforce a startup grace period during which
    # no restart can be issued, even if metrics look frozen.
    window_start_ts: float = field(default_factory=time.time)
    # When BOTH metrics return None (target scraped down / disappeared from
    # Prometheus). This covers `docker stop hydra-bot` and similar full-loss
    # scenarios where there is no metric value to compare against at all.
    target_down_since: Optional[float] = None


class Watchdog:
    """Decision loop driver.

    `tick()` performs ONE decision cycle given an injectable `now` — the
    primary unit-test entry point. `run()` wraps it in a real-time loop.
    """

    HEALTH_METRIC = "hydra_health_status"
    HEARTBEAT_METRIC = "hydra_heartbeat_timestamp"

    def __init__(
        self,
        config: WatchdogConfig,
        prom: PrometheusClient,
        controller: ContainerController,
    ):
        self.cfg = config
        self.prom = prom
        self.controller = controller
        self.state = _State()

    # ----------------------------------------------------------------
    # decision tick
    # ----------------------------------------------------------------
    def tick(self, now: Optional[float] = None) -> bool:
        """Run one decision cycle. Returns True iff a restart was issued."""
        if now is None:
            now = time.time()

        # Cooldown after recent restart — don't re-fire instantly
        if (
            self.state.last_restart_ts
            and now - self.state.last_restart_ts < self.cfg.restart_cooldown_sec
        ):
            return False

        # Startup / post-restart grace window: no restart can be issued while
        # the bot is still booting (Prometheus scrape interval, WS connect,
        # market load). Metrics tracking still runs to seed the baseline.
        in_grace = (now - self.state.window_start_ts) < self.cfg.startup_grace_sec

        health = self.prom.query_scalar(self.HEALTH_METRIC)
        heartbeat = self.prom.query_scalar(self.HEARTBEAT_METRIC)

        # ---- Target-down tracking ----
        # If BOTH metric series have disappeared from Prometheus, the bot is
        # almost certainly stopped or unreachable. Treat as a freeze signal.
        if health is None and heartbeat is None:
            if self.state.target_down_since is None:
                self.state.target_down_since = now
        else:
            self.state.target_down_since = None

        # ---- Health gauge tracking ----
        if health is not None:
            if health <= 0.0:
                if self.state.health_bad_since is None:
                    self.state.health_bad_since = now
            else:
                self.state.health_bad_since = None

        # ---- Heartbeat freeze tracking ----
        # heartbeat is a Unix timestamp gauge updated every main-loop iteration.
        # If the bot is alive (IDLE, SCANNING, BUYING, IN_POSITION, EXITING)
        # it updates heartbeat ~every 30-60s.  If stuck/dead, heartbeat grows stale.
        if heartbeat is not None:
            # Record that we have seen a valid heartbeat sample
            self.state.last_heartbeat_ts = heartbeat

        if in_grace:
            return False

        # ---- Decide ----
        threshold = self.cfg.stall_threshold_sec
        health_stalled = (
            self.state.health_bad_since is not None
            and (now - self.state.health_bad_since) >= threshold
        )
        # Heartbeat is stale if we have seen at least one sample and it is
        # older than the threshold.  This covers IN_POSITION (no scanning)
        # because heartbeat is updated on every loop iteration regardless of state.
        heartbeat_stalled = (
            self.state.last_heartbeat_ts is not None
            and (now - self.state.last_heartbeat_ts) >= threshold
        )
        target_down = (
            self.state.target_down_since is not None
            and (now - self.state.target_down_since) >= threshold
        )

        if health_stalled or heartbeat_stalled or target_down:
            reason = []
            if health_stalled:
                reason.append(
                    f"health=0 for {now - self.state.health_bad_since:.1f}s"
                )
            if heartbeat_stalled:
                reason.append(
                    f"heartbeat stale for {now - self.state.last_heartbeat_ts:.1f}s"
                )
            if target_down:
                reason.append(
                    f"target unreachable for {now - self.state.target_down_since:.1f}s"
                )
            logger.critical(
                "@WATCHDOG_RESTART@ Forcing restart of %s (%s)",
                self.cfg.target_container,
                "; ".join(reason),
            )
            try:
                self.controller.restart(
                    self.cfg.target_container, self.cfg.restart_timeout_sec
                )
            except Exception as e:
                logger.error("@WATCHDOG_RESTART_FAIL@ %s", e)
                return False

            # Reset state and start cooldown + new grace window
            self.state = _State(last_restart_ts=now, window_start_ts=now)
            return True

        return False

    # ----------------------------------------------------------------
    # production loop
    # ----------------------------------------------------------------
    def run(self) -> None:  # pragma: no cover - infinite loop
        logger.info(
            "@WATCHDOG_START@ prometheus=%s target=%s poll=%.1fs stall=%.1fs",
            self.cfg.prometheus_url,
            self.cfg.target_container,
            self.cfg.poll_interval_sec,
            self.cfg.stall_threshold_sec,
        )
        while True:
            try:
                self.tick()
            except Exception as e:
                logger.error("@WATCHDOG_TICK_ERROR@ %s", e, exc_info=True)
            time.sleep(self.cfg.poll_interval_sec)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:  # pragma: no cover - thin wrapper
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    cfg = WatchdogConfig()
    prom = PrometheusClient(cfg.prometheus_url, cfg.request_timeout_sec)
    controller = DockerContainerController()
    Watchdog(cfg, prom, controller).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
