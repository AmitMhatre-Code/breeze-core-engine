"""Rolling-minute window, critical/advisory shedding, and the reserved daily floor.

The pre-existing pacer only remembered the *last* call, so it could space requests but
never see a throttle coming — it reacted after ICICI returned 429 and then gave up in ~4s
against a minute-scale cooldown. These cover the proactive half, plus the classification
that decides who loses a slot when there are not enough.
"""
from __future__ import annotations

import pytest

from icici_breeze_backend.app.services import icici_api_pacing as pacing
from icici_breeze_backend.app.services.icici_api_pacing import GlobalIciciApiPacer
from icici_breeze_backend.app.services.icici_call_class import (
    ADVISORY,
    CRITICAL,
    advisory_calls,
    critical_calls,
    current_call_class,
    is_advisory,
)

USER = "VIKRAMMH"


@pytest.fixture(autouse=True)
def _reset_pacer():
    GlobalIciciApiPacer.reset_user(USER)
    yield
    GlobalIciciApiPacer.reset_user(USER)


def _fill_window(n: int) -> None:
    for _ in range(n):
        GlobalIciciApiPacer.note_call(USER)


# ------------------------------------------------------------------ classification


def test_unmarked_calls_are_critical():
    """Fail-safe direction: shedding must be opt-in, so an unaudited call site keeps
    working exactly as it does today."""
    assert current_call_class() == CRITICAL
    assert is_advisory() is False


def test_advisory_scope_applies_and_unwinds():
    with advisory_calls():
        assert current_call_class() == ADVISORY
    assert current_call_class() == CRITICAL


def test_critical_scope_nests_inside_advisory():
    with advisory_calls():
        with critical_calls():
            assert is_advisory() is False
        assert is_advisory() is True


# ------------------------------------------------------------------ rolling window


def test_window_counts_recent_calls():
    _fill_window(5)
    assert GlobalIciciApiPacer.calls_in_window(USER) == 5


def test_window_is_per_user():
    _fill_window(5)
    assert GlobalIciciApiPacer.calls_in_window("SOMEONE_ELSE") == 0


def test_slot_granted_below_the_ceiling():
    _fill_window(10)
    assert GlobalIciciApiPacer.wait_for_minute_slot(USER, advisory=True) is True


def test_advisory_is_shed_when_the_window_is_full():
    _fill_window(pacing._MAX_CALLS_PER_MINUTE)
    assert GlobalIciciApiPacer.wait_for_minute_slot(USER, advisory=True) is False


def test_critical_is_never_shed(monkeypatch):
    """A critical call waits for a slot and, if the window stays full, goes anyway —
    refusing to place an exit order because of our own bookkeeping would be worse than
    risking an ICICI throttle we can still retry through."""
    monkeypatch.setattr(pacing, "_MAX_SLOT_WAIT_SEC", 0.01)
    _fill_window(pacing._MAX_CALLS_PER_MINUTE)
    assert GlobalIciciApiPacer.wait_for_minute_slot(USER, advisory=False) is True


def test_granting_a_slot_consumes_it():
    _fill_window(pacing._MAX_CALLS_PER_MINUTE - 1)
    assert GlobalIciciApiPacer.wait_for_minute_slot(USER, advisory=True) is True
    assert GlobalIciciApiPacer.wait_for_minute_slot(USER, advisory=True) is False


def test_old_calls_age_out_of_the_window(monkeypatch):
    _fill_window(pacing._MAX_CALLS_PER_MINUTE)
    assert GlobalIciciApiPacer.wait_for_minute_slot(USER, advisory=True) is False
    # Shrink the window instead of sleeping 60s; same pruning path.
    monkeypatch.setattr(pacing, "_RATE_WINDOW_SEC", 0.0)
    assert GlobalIciciApiPacer.wait_for_minute_slot(USER, advisory=True) is True


# ------------------------------------------------------------------ reserved daily floor


def test_advisory_budget_exhausts_before_the_hard_cap(monkeypatch):
    from icici_breeze_backend.app.services import api_usage

    monkeypatch.setattr(api_usage, "get_today_count", lambda uid: api_usage.AMBER_MAX)
    assert api_usage.advisory_budget_exhausted(USER) is True
    assert api_usage.is_daily_limit_reached(USER) is False, (
        "critical calls must still be allowed inside the reserve"
    )


def test_reserve_is_not_triggered_below_the_line(monkeypatch):
    from icici_breeze_backend.app.services import api_usage

    monkeypatch.setattr(api_usage, "get_today_count", lambda uid: api_usage.AMBER_MAX - 1)
    assert api_usage.advisory_budget_exhausted(USER) is False


def test_shed_error_is_not_reported_as_a_broker_throttle():
    """Nothing failed and ICICI was never asked — calling it a throttle would send the
    user chasing a problem at the broker that does not exist."""
    err = pacing.GlobalIciciApiLimiter.build_shed_error(USER, endpoint="get_order_list")
    assert err["advisory_shed"] is True
    assert err["icici_throttled"] is False
    assert "throttled by ICICI" not in err["Error"]
