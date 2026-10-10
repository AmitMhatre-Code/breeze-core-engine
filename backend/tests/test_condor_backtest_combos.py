"""The condor backtest's settings comparison (#71): which combinations are built around the
saved settings, that replaying them together on one source changes no result and fetches each
window once, and that a stored row is evidence for exactly its own settings."""
from __future__ import annotations

import datetime

import pytest

from icici_breeze_backend.app.domain.condor import CondorSettings, DynamicCondorBotConfig
from icici_breeze_backend.app.services.bots import backtest_jobs as jobs
from icici_breeze_backend.app.services.bots.scalping import backtest_store as store
from icici_breeze_backend.app.services.condor import backtest_combos, backtest_job
from icici_breeze_backend.app.services.condor import bot as condor_bot
from icici_breeze_backend.app.services.condor.backtest import CondorReplay
from icici_breeze_backend.app.services.condor.engine import ENGINE_VERSION
from tests.test_condor_backtest import CHARGES, SPREAD, PathSource, flat, whipsaw

START = datetime.date(2026, 3, 2)
END = datetime.date(2026, 5, 29)


def _values(combos, field):
    return sorted({getattr(c.settings, field) for c in combos})


def test_the_default_grid_is_108_combinations_around_the_saved_settings():
    saved = CondorSettings(margin_ceiling_inr=10_00_000)
    every = backtest_combos.combos_for(saved, "time_roll")
    # The 108-row grid, then the premium gate's rows (#78), one factor at a time.
    combos = [c for c in every if not c.id.startswith("premium-")]
    assert len(combos) == 108 == len({c.id for c in combos})
    assert [c.id for c in every[108:]] == ["premium-0.90", "premium-1.00", "premium-1.20"]
    assert _values(combos, "net_delta_band_per_lot") == [0.1, 0.15, 0.2]
    assert _values(combos, "min_roll_credit_points") == [10.0, 20.0, 30.0]
    assert _values(combos, "max_loss_pct_of_ceiling") == [2.5, 5.0, 7.5]
    assert _values(combos, "no_roll_within_days_of_exit") == [0, 3]
    assert {c.exit_action for c in combos} == {"time_roll", "close"}
    [mine] = [c for c in combos if c.is_saved]
    assert mine.settings == saved and mine.exit_action == "time_roll"
    # Nothing outside the five is ever changed.
    fixed = set(CondorSettings.model_fields) - {
        "net_delta_band_per_lot", "min_roll_credit_points", "max_loss_pct_of_ceiling", "no_roll_within_days_of_exit",
    }
    assert all(getattr(c.settings, f) == getattr(saved, f) for c in combos for f in fixed)


def test_a_step_outside_the_allowed_range_is_dropped_not_shifted():
    saved = CondorSettings(
        margin_ceiling_inr=10_00_000, net_delta_band_per_lot=0.05, min_roll_credit_points=0,
        max_loss_pct_of_ceiling=80, no_roll_within_days_of_exit=5,
    )
    combos = [c for c in backtest_combos.combos_for(saved, "close") if not c.id.startswith("premium-")]
    assert _values(combos, "net_delta_band_per_lot") == [0.05, 0.1]
    assert _values(combos, "min_roll_credit_points") == [0.0, 10.0]
    assert _values(combos, "max_loss_pct_of_ceiling") == [40.0, 80.0]
    assert _values(combos, "no_roll_within_days_of_exit") == [0, 3, 5]
    assert len(combos) == 2 * 2 * 2 * 3 * 2
    assert sum(c.is_saved for c in combos) == 1


def test_max_loss_varies_the_percent_form_and_leaves_a_rupee_limit_alone():
    saved = CondorSettings(margin_ceiling_inr=10_00_000, max_loss_inr=40_000, max_loss_pct_of_ceiling=5)
    combos = backtest_combos.combos_for(saved, "time_roll")
    assert _values(combos, "max_loss_pct_of_ceiling") == [2.5, 5.0, 7.5]
    assert _values(combos, "max_loss_inr") == [40_000]


def test_with_only_a_rupee_limit_the_rupee_limit_varies():
    saved = CondorSettings(margin_ceiling_inr=10_00_000, max_loss_inr=40_000, max_loss_pct_of_ceiling=None)
    combos = backtest_combos.combos_for(saved, "time_roll")
    assert _values(combos, "max_loss_inr") == [20_000, 40_000, 60_000]
    assert {c.settings.max_loss_pct_of_ceiling for c in combos} == {None}
    assert "Max loss ₹20,000" in combos[0].label


# --------------------------------------------------------------------------------------
# Replaying them together
# --------------------------------------------------------------------------------------


class _Fetcher:
    def __init__(self, source):
        self.source = source
        self.windows: list = []

    def fetch_needs(self, needs):
        needs = list(needs)
        self.windows.extend(needs)
        self.source.fetch(needs)
        return {"fetched": len(needs)}


@pytest.fixture
def quiet_job(monkeypatch):
    monkeypatch.setattr(jobs, "_memory_check", lambda stage: None)
    monkeypatch.setattr(store, "add_needs", lambda needs, path=None: None)
    jobs._cancel.clear()


def _replay(settings, exit_action, source):
    return CondorReplay(
        settings, START, END, lots_per_tranche=2, exit_action=exit_action, source=source,
        charges=CHARGES, spread=SPREAD, holidays=set(),
    )


def _alone(settings, exit_action, path):
    source = PathSource(path)
    replay = _replay(settings, exit_action, source)
    while needs := replay.run():
        source.fetch(needs)
    return replay.summary()


def test_replaying_together_matches_replaying_alone_and_fetches_each_window_once(quiet_job):
    saved = CondorSettings(margin_ceiling_inr=60_00_000)
    combos = backtest_combos.combos_for(saved, "time_roll")[::9]  # a spread of the grid
    source = PathSource(whipsaw)
    fetcher = _Fetcher(source)
    replays = [(c, _replay(c.settings, c.exit_action, source)) for c in combos]
    notes: list[str] = []
    assert backtest_job._drive(replays, source, fetcher, notes) == {}
    assert notes == []
    assert len(fetcher.windows) == len(set(fetcher.windows))
    for combo, replay in replays:
        assert replay.done
        assert replay.summary() == _alone(combo.settings, combo.exit_action, whipsaw), combo.label


def test_nothing_to_fetch_leaves_every_combination_partial_and_says_where(quiet_job):
    saved = CondorSettings(margin_ceiling_inr=60_00_000)
    combos = backtest_combos.combos_for(saved, "time_roll")[:4]
    source = PathSource(flat)
    replays = [(c, _replay(c.settings, c.exit_action, source)) for c in combos]
    notes: list[str] = []
    stopped = backtest_job._drive(replays, source, None, notes)
    assert set(stopped) == {c.id for c in combos}
    assert "4 of 4 combination(s) stopped part-way" in notes[0]


def test_a_cancel_stops_between_combinations(quiet_job):
    saved = CondorSettings(margin_ceiling_inr=60_00_000)
    source = PathSource(flat, everything_cached=True)
    replays = [(c, _replay(c.settings, c.exit_action, source)) for c in backtest_combos.combos_for(saved, "close")[:3]]
    jobs._cancel.set()
    try:
        with pytest.raises(RuntimeError, match="Stopped at your request"):
            backtest_job._drive(replays, source, None, [])
    finally:
        jobs._cancel.clear()


# --------------------------------------------------------------------------------------
# Evidence
# --------------------------------------------------------------------------------------


def _stored_run(saved: CondorSettings, exit_action: str, *, incomplete: set[str] = frozenset()):
    rows = []
    for c in backtest_combos.combos_for(saved, exit_action):
        complete = c.id not in incomplete
        rows.append(backtest_combos.comparison_row(
            c, {"max_drawdown": 1234.0, "closed_pnl": 10.0}, [{"net_pnl": 5.0}], complete=complete,
        ))
    return {
        "id": "run-1", "created_at": "2026-10-05T18:00:00", "status": "partial" if incomplete else "completed",
        "params": {"settings": saved.model_dump(mode="json"), "exit_action": exit_action,
                   "engine_version": ENGINE_VERSION, "from": "2026-03-02", "to": "2026-05-29"},
        "summary": {"comparison": rows},
    }


def _evidence(monkeypatch, run, campaign: CondorSettings, exit_action: str):
    monkeypatch.setattr(backtest_job, "list_runs", lambda user_id, limit=None: [run])
    monkeypatch.setattr(condor_bot.repo, "campaigns_with_note", lambda user_id, note: [])
    cfg = DynamicCondorBotConfig(campaign=campaign, exit_action=exit_action)
    return condor_bot.eligibility("u1", cfg).backtest


def test_a_neighbouring_combination_is_evidence_once_its_settings_are_saved(monkeypatch):
    saved = CondorSettings(margin_ceiling_inr=10_00_000)
    neighbour = saved.model_copy(update={"net_delta_band_per_lot": 0.2, "min_roll_credit_points": 10.0,
                                         "max_loss_pct_of_ceiling": 7.5, "no_roll_within_days_of_exit": 3})
    # Re-validated the way a save through the settings drawer would arrive.
    neighbour = CondorSettings(**neighbour.model_dump(mode="json"))
    found = _evidence(monkeypatch, _stored_run(saved, "time_roll"), neighbour, "close")
    assert found is not None
    assert found["combination"].startswith("Band 0.2 · Roll credit 10 · Max loss 7.5% · No-roll 3d · Close")
    assert found["net_pnl"] == 5.0 and found["max_drawdown"] == 1234.0


def test_settings_outside_the_grid_or_an_unfinished_row_are_not_evidence(monkeypatch):
    saved = CondorSettings(margin_ceiling_inr=10_00_000)
    elsewhere = saved.model_copy(update={"short_delta": 0.25})
    assert _evidence(monkeypatch, _stored_run(saved, "time_roll"), elsewhere, "time_roll") is None
    mine = next(c for c in backtest_combos.combos_for(saved, "time_roll") if c.is_saved)
    run = _stored_run(saved, "time_roll", incomplete={mine.id})
    assert _evidence(monkeypatch, run, saved, "time_roll") is None


def test_a_run_from_before_the_comparison_is_still_evidence_for_its_own_settings(monkeypatch):
    saved = CondorSettings(margin_ceiling_inr=10_00_000)
    run = {
        "id": "old", "created_at": "2026-10-04T18:00:00", "status": "completed",
        "params": {"settings": saved.model_dump(mode="json"), "exit_action": "time_roll",
                   "engine_version": ENGINE_VERSION},
        "summary": {"closed_pnl": 1.0, "net_pnl": 2.0, "max_drawdown": 3.0},
    }
    found = _evidence(monkeypatch, run, saved, "time_roll")
    assert found["run_id"] == "old" and found["net_pnl"] == 2.0
