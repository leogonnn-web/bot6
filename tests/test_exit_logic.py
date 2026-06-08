"""Tests for circuit-breaker + limit-chaser pure helpers (shared/utils.py)."""
import pytest
from utils import (
    panic_window_stats,
    circuit_breaker_tripped,
    chase_deadline,
    exit_backstop_decision,
)


class TestPanicWindowStats:
    def test_counts_only_within_window(self):
        now = 1000.0
        events = [(400.0, -0.5), (500.0, -0.3), (995.0, -0.2)]  # first two are >300s old
        count, total = panic_window_stats(events, now, window_sec=300)
        assert count == 1
        assert total == pytest.approx(-0.2)

    def test_sums_signed_pnl(self):
        now = 1000.0
        events = [(990.0, -0.4), (995.0, 0.1)]
        count, total = panic_window_stats(events, now, window_sec=600)
        assert count == 2
        assert total == pytest.approx(-0.3)

    def test_empty(self):
        assert panic_window_stats([], 1000.0, 600) == (0, 0.0)


class TestCircuitBreakerTripped:
    def test_trips_on_count(self):
        now = 1000.0
        events = [(now - i, -0.2) for i in range(6)]
        tripped, reason = circuit_breaker_tripped(events, now, 600, max_panics=6, max_loss_usd=100)
        assert tripped and "panics" in reason

    def test_trips_on_loss(self):
        now = 1000.0
        events = [(now - 10, -2.0), (now - 5, -1.5)]
        tripped, reason = circuit_breaker_tripped(events, now, 600, max_panics=99, max_loss_usd=3.0)
        assert tripped and "loss" in reason

    def test_not_tripped_below_thresholds(self):
        now = 1000.0
        events = [(now - 10, -0.5), (now - 5, -0.5)]
        tripped, _ = circuit_breaker_tripped(events, now, 600, max_panics=6, max_loss_usd=3.0)
        assert not tripped

    def test_old_events_age_out(self):
        now = 1000.0
        events = [(now - 700, -5.0)] * 10  # all outside 600s window
        tripped, _ = circuit_breaker_tripped(events, now, 600, max_panics=3, max_loss_usd=3.0)
        assert not tripped


class TestChaseDeadline:
    def test_urgent_shorter_than_normal(self):
        now = 100.0
        assert chase_deadline(now, True, 3.0, 12.0) == 103.0
        assert chase_deadline(now, False, 3.0, 12.0) == 112.0


class TestExitBackstopDecision:
    def test_hard_adverse_urgent_triggers_market(self):
        bs, reason = exit_backstop_decision(100.0, 98.0, urgent=True,
                                            urgent_skip_below_pct=1.5, deadline_passed=False)
        assert bs and "hard_adverse" in reason

    def test_non_urgent_ignores_hard_adverse(self):
        # Non-urgent exit keeps chasing even on a big drop (until deadline)
        bs, _ = exit_backstop_decision(100.0, 98.0, urgent=False,
                                       urgent_skip_below_pct=1.5, deadline_passed=False)
        assert not bs

    def test_deadline_triggers_backstop(self):
        bs, reason = exit_backstop_decision(100.0, 99.9, urgent=True,
                                            urgent_skip_below_pct=1.5, deadline_passed=True)
        assert bs and reason == "deadline"

    def test_mild_drop_keeps_chasing(self):
        bs, _ = exit_backstop_decision(100.0, 99.0, urgent=True,
                                       urgent_skip_below_pct=1.5, deadline_passed=False)
        assert not bs
