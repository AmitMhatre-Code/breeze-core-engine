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
