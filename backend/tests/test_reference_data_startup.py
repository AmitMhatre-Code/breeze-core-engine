"""Startup reference data bootstrap: skip network load when Redis is complete."""
from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import MagicMock, patch


import pytest

_ORCH = "icici_breeze_backend.app.services.reference_data.orchestrator"


@pytest.fixture(autouse=True)
def _restore_orchestrator_module():
    """`_scheduler_module` swaps a fake orchestrator into sys.modules; put the real one back
    so later test files do not import the fake."""
    real = sys.modules.get(_ORCH)
    yield
    if real is not None:
        sys.modules[_ORCH] = real
    else:
        sys.modules.pop(_ORCH, None)


def _scheduler_module():
    fake_orchestrator = ModuleType(
        "icici_breeze_backend.app.services.reference_data.orchestrator"
    )
    fake_orchestrator.run_reference_data_load = MagicMock(return_value={"ok": True})
    fake_orchestrator.stale_reference_sources = MagicMock(return_value=[])
    sys.modules[
        "icici_breeze_backend.app.services.reference_data.orchestrator"
    ] = fake_orchestrator
    from icici_breeze_backend.app.services.reference_data import scheduler

    return scheduler, fake_orchestrator


def test_bootstrap_skips_network_load_when_complete():
    scheduler, orchestrator = _scheduler_module()

    with patch.object(scheduler, "bootstrap_reference_data_schedule"), patch(
        "icici_breeze_backend.app.services.reference_data.cache_bootstrap.ensure_all_reference_data_cached"
    ), patch(
        "icici_breeze_backend.app.services.reference_data.cache_bootstrap.is_reference_data_complete",
        return_value=True,
    ):
        scheduler.bootstrap_reference_data_on_startup()
    orchestrator.run_reference_data_load.assert_not_called()


def test_bootstrap_runs_network_load_when_incomplete():
    scheduler, orchestrator = _scheduler_module()

    with patch.object(scheduler, "bootstrap_reference_data_schedule"), patch(
        "icici_breeze_backend.app.services.reference_data.cache_bootstrap.ensure_all_reference_data_cached"
    ), patch(
        "icici_breeze_backend.app.services.reference_data.cache_bootstrap.is_reference_data_complete",
        return_value=False,
    ):
        scheduler.bootstrap_reference_data_on_startup()
    orchestrator.run_reference_data_load.assert_called_once_with(
        force=True, trigger_mode="startup"
    )


def _bootstrap(scheduler, *, complete):
    with patch.object(scheduler, "bootstrap_reference_data_schedule"), patch(
        "icici_breeze_backend.app.services.reference_data.cache_bootstrap.ensure_all_reference_data_cached"
    ), patch(
        "icici_breeze_backend.app.services.reference_data.cache_bootstrap.is_reference_data_complete",
        return_value=complete,
    ), patch.object(scheduler, "_start_startup_refresh") as background:
        scheduler.bootstrap_reference_data_on_startup()
    return background


def test_a_complete_but_stale_cache_is_refreshed_in_the_background():
    """B-57: startup checked that data was present, not how old it was, so an instance off at
    18:00 kept a three-day-old scrip master and new strikes had no quote."""
    scheduler, orchestrator = _scheduler_module()
    orchestrator.stale_reference_sources.return_value = ["scrip"]
    background = _bootstrap(scheduler, complete=True)
    background.assert_called_once_with(first_wait=0)
    orchestrator.run_reference_data_load.assert_not_called()  # not on the startup path itself


def test_a_failed_startup_load_is_retried_in_the_background():
    scheduler, orchestrator = _scheduler_module()
    orchestrator.run_reference_data_load.return_value = {"ok": False}
    background = _bootstrap(scheduler, complete=False)
    background.assert_called_once_with(first_wait=scheduler._STARTUP_RETRY_WAITS_SECONDS[0])


def test_a_good_startup_load_needs_no_retry():
    scheduler, _orchestrator = _scheduler_module()
    background = _bootstrap(scheduler, complete=False)
    background.assert_not_called()


def test_scheduler_loop_survives_a_failed_tick(monkeypatch):
    """B-28: `load_schedule()` ran with no `try`, so one "database is locked" ended the
    daily reference-data scheduler for the life of the process."""
    from icici_breeze_backend.app.services.reference_data import scheduler

    calls = []

    class _Stop:
        def is_set(self):
            return len(calls) >= 2

        def wait(self, _seconds):
            return None

    def flaky_tick():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("database is locked")

    monkeypatch.setattr(scheduler, "_stop", _Stop())
    monkeypatch.setattr(scheduler, "_scheduler_tick", flaky_tick)
    scheduler._scheduler_loop()
    assert len(calls) == 2
