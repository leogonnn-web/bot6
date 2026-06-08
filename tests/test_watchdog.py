"""Unit tests for src/monitoring/watchdog.py — heartbeat-based watchdog.

Mocks the Prometheus client and container controller so the tests run
without docker, network, or a live Prometheus server.
"""
from __future__ import annotations

import os
import sys
from typing import Dict, List, Optional

import pytest

# Ensure repo root on sys.path so `src.monitoring.watchdog` resolves
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.monitoring.watchdog import Watchdog, WatchdogConfig  # noqa: E402


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeProm:
    """Returns scripted scalar values per metric, advancing on each call."""

    def __init__(self, values: Dict[str, List[Optional[float]]]):
        # Each key maps to a list popped from the front per call.
        self.values = {k: list(v) for k, v in values.items()}
        self.calls: List[str] = []

    def query_scalar(self, metric: str) -> Optional[float]:
        self.calls.append(metric)
        bucket = self.values.get(metric, [])
        if not bucket:
            return None
        # Sticky last value once exhausted
        if len(bucket) == 1:
            return bucket[0]
        return bucket.pop(0)


class FakeController:
    def __init__(self):
        self.restart_calls: List[tuple] = []
        self.raise_on_restart: Optional[Exception] = None

    def restart(self, name: str, timeout: int) -> None:
        if self.raise_on_restart:
            raise self.raise_on_restart
        self.restart_calls.append((name, timeout))


def _make_watchdog(prom_values: Dict[str, List[Optional[float]]],
                   stall: float = 15.0,
                   cooldown: float = 60.0,
                   grace: float = 0.0):
    cfg = WatchdogConfig(
        prometheus_url="http://test",
        target_container="hydra-bot",
        poll_interval_sec=1.0,
        stall_threshold_sec=stall,
        restart_cooldown_sec=cooldown,
        restart_timeout_sec=2,
        request_timeout_sec=1.0,
        startup_grace_sec=grace,
    )
    prom = FakeProm(prom_values)
    ctrl = FakeController()
    wd = Watchdog(cfg, prom, ctrl)
    # Tests use a virtual clock starting at 0.0 — anchor the window there too,
    # otherwise the real-time default (time.time()) would put `now=0.0` deep
    # in the past and silently disable the grace logic.
    wd.state.window_start_ts = 0.0
    return wd, prom, ctrl


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_no_restart_when_healthy_and_heartbeat_fresh():
    """Healthy + heartbeat fresh (updated every loop) → no restart."""
    wd, prom, ctrl = _make_watchdog({
        "hydra_health_status": [1.0],
        # heartbeat timestamps advancing — bot is alive in any state
        "hydra_heartbeat_timestamp": [0.0, 5.0, 10.0, 20.0],
    })
    for t in (0.0, 5.0, 10.0, 20.0):
        assert wd.tick(now=t) is False
    assert ctrl.restart_calls == []


def test_restart_on_health_zero_for_threshold():
    """health_status stuck at 0 for >= stall_threshold_sec → restart."""
    wd, prom, ctrl = _make_watchdog({
        "hydra_health_status": [0.0],
        # heartbeat still updating — bot is alive but unhealthy internally
        "hydra_heartbeat_timestamp": [0.0, 5.0, 10.0, 16.0],
    })
    assert wd.tick(now=0.0) is False    # bad-since starts
    assert wd.tick(now=10.0) is False   # 10s < 15s threshold
    assert wd.tick(now=15.5) is True    # crossed threshold → restart
    assert ctrl.restart_calls == [("hydra-bot", 2)]


def test_restart_on_heartbeat_stale():
    """Heartbeat gauge not updated for >= threshold → restart.
    This covers IN_POSITION where scan_cycles would not advance.
    """
    wd, prom, ctrl = _make_watchdog({
        "hydra_health_status": [1.0],
        # heartbeat stuck at 0 — bot frozen (e.g. IN_POSITION deadlock)
        "hydra_heartbeat_timestamp": [0.0],
    })
    assert wd.tick(now=0.0) is False    # baseline sample taken
    assert wd.tick(now=10.0) is False   # not yet 15s
    assert wd.tick(now=16.0) is True    # heartbeat stale → restart
    assert ctrl.restart_calls == [("hydra-bot", 2)]


def test_no_restart_in_position_with_fresh_heartbeat():
    """IN_POSITION: scan_cycles would freeze, but heartbeat is fresh → no restart.
    Regression test for the prod restart-loop bug.
    """
    wd, prom, ctrl = _make_watchdog({
        "hydra_health_status": [1.0],
        # Bot in IN_POSITION for 30 min — heartbeat still updating every loop
        "hydra_heartbeat_timestamp": [0.0, 10.0, 20.0, 30.0, 40.0, 50.0],
    })
    for t in (0.0, 10.0, 20.0, 30.0, 40.0, 50.0):
        assert wd.tick(now=t) is False
    assert ctrl.restart_calls == []


def test_health_recovers_resets_timer():
    """If health flips back to 1, the bad_since timer resets — no restart."""
    wd, prom, ctrl = _make_watchdog({
        "hydra_health_status": [0.0, 0.0, 1.0, 1.0, 1.0],
        "hydra_heartbeat_timestamp": [0.0, 5.0, 10.0, 15.0, 20.0],
    })
    wd.tick(now=0.0)
    wd.tick(now=5.0)
    wd.tick(now=10.0)   # now health=1 → reset
    wd.tick(now=20.0)
    wd.tick(now=30.0)
    assert ctrl.restart_calls == []


def test_cooldown_blocks_double_restart():
    """After a restart, no second restart fires during cooldown window."""
    wd, prom, ctrl = _make_watchdog(
        {
            "hydra_health_status": [0.0],
            "hydra_heartbeat_timestamp": [0.0],
        },
        stall=15.0,
        cooldown=60.0,
    )
    # First trigger
    wd.tick(now=0.0)
    assert wd.tick(now=20.0) is True
    # State reset; conditions reappear but cooldown blocks restart
    wd.tick(now=21.0)
    assert wd.tick(now=40.0) is False  # within 60s cooldown of last restart
    assert len(ctrl.restart_calls) == 1


def test_restart_failure_does_not_reset_state():
    """If docker restart raises, state is NOT reset — issue retries next tick."""
    wd, prom, ctrl = _make_watchdog({
        "hydra_health_status": [0.0],
        "hydra_heartbeat_timestamp": [0.0],
    })
    ctrl.raise_on_restart = RuntimeError("docker daemon down")

    wd.tick(now=0.0)
    result = wd.tick(now=20.0)
    assert result is False
    assert ctrl.restart_calls == []
    # health_bad_since still set → retry possible on next tick once docker recovers
    assert wd.state.health_bad_since is not None


def test_heartbeat_reset_on_bot_restart_does_not_trigger():
    """When hydra-bot restarts, heartbeat resets to ~0 or old value.
    Watchdog must not treat lower/fresh heartbeat as stall.
    Grace period covers startup; afterwards fresh heartbeat = alive.
    """
    wd, prom, ctrl = _make_watchdog({
        "hydra_health_status": [1.0],
        # Old heartbeat → bot restarts → new heartbeat starts from 0
        "hydra_heartbeat_timestamp": [1000.0, 1000.0, 0.0, 5.0, 10.0],
    })
    wd.tick(now=0.0)     # old heartbeat 1000
    wd.tick(now=5.0)     # still old
    # At t=10 bot restarts, heartbeat now 0 (new process) but inside grace
    wd.tick(now=10.0)    # 0 — new process, grace protects
    wd.tick(now=15.0)    # 5 — fresh
    wd.tick(now=20.0)    # 10 — fresh
    assert ctrl.restart_calls == []


def test_startup_grace_blocks_restart():
    """During the startup grace window, no restart fires even on stall."""
    wd, prom, ctrl = _make_watchdog(
        {
            "hydra_health_status": [0.0],         # bad from the start
            "hydra_heartbeat_timestamp": [0.0],   # frozen
        },
        grace=30.0,
    )
    # Past stall threshold (15s) but still inside grace (30s) → no restart
    assert wd.tick(now=0.0) is False
    assert wd.tick(now=20.0) is False
    # Past grace (30s) AND past threshold → restart fires
    assert wd.tick(now=35.0) is True
    assert len(ctrl.restart_calls) == 1


def test_prometheus_unreachable_does_not_crash():
    """If both queries return None, watchdog must not raise.

    With the target-down detection enabled, sustained unreachability beyond
    the stall threshold is itself a restart trigger; this test only asserts
    the no-crash invariant during the early window.
    """
    wd, prom, ctrl = _make_watchdog(
        {
            "hydra_health_status": [None],
            "hydra_heartbeat_timestamp": [None],
        },
        grace=120.0,  # keep us inside grace so no restart fires
    )
    for t in (0.0, 10.0, 30.0, 60.0):
        assert wd.tick(now=t) is False
    assert ctrl.restart_calls == []


def test_target_down_triggers_restart():
    """When the bot disappears from Prometheus (e.g. `docker stop`), watchdog
    must detect target unreachable and restart after stall_threshold_sec.

    Regression for the prod test where `docker stop hydra-bot` did not fire
    a restart because both metric queries returned empty result sets.
    """
    wd, prom, ctrl = _make_watchdog({
        "hydra_health_status": [None],
        "hydra_heartbeat_timestamp": [None],
    })
    assert wd.tick(now=0.0) is False    # arms target_down_since
    assert wd.tick(now=10.0) is False   # 10s < 15s threshold
    assert wd.tick(now=15.5) is True    # crossed threshold → restart
    assert ctrl.restart_calls == [("hydra-bot", 2)]
