"""Startup reference data bootstrap: skip network load when Redis is complete."""
from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import MagicMock, patch


def _scheduler_module():
    fake_orchestrator = ModuleType(
        "icici_breeze_backend.app.services.reference_data.orchestrator"
    )
    fake_orchestrator.run_reference_data_load = MagicMock()
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
