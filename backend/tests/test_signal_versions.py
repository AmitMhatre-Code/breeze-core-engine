"""Signal versions running side by side, as bots and the Signals page see them (#72)."""
from __future__ import annotations

import pytest

from icici_breeze_backend.app.api.v1.route_signals import _run_summary
from icici_breeze_backend.app.db.bots_migrate import BOT_CAS_BINGO, BOT_MOMENTUM_LONG_SCALPER
from icici_breeze_backend.app.domain.bots import (
    CasBingoConfig,
    CasBingoIndex,
    MomentumLongScalperConfig,
    SignalChoice,
)
from icici_breeze_backend.app.services.bots import backtest_combos, signal_gate
from icici_breeze_backend.app.services.index_signal import gate
from icici_breeze_backend.app.services.index_signal.mechanisms import SeriesKey


@pytest.fixture(autouse=True)
def _closed_gate(monkeypatch):
    """No backtest has run: every version is closed to bots."""
    monkeypatch.setattr(gate, "refusal", lambda mechanism, version=None, **kw: f"closed v{version}")


def _v3(duration: int = 1) -> SignalChoice:
    return SignalChoice(mechanism="momentum", version=3, duration=duration)


def test_a_choice_names_its_version_and_expansion_has_only_one():
    assert SignalChoice(mechanism="expansion", version=1).version == 3
    with pytest.raises(ValueError, match="no version 4"):
        SignalChoice(mechanism="momentum", version=4)
    assert _v3(5).label() == "Momentum v3 5m"
    assert _v3(5).series_id("NIFTY") == "nifty:momentum:v3:5m"


def test_cas_bingo_refuses_momentum_v3_while_sensex_is_switched_on():
    config = CasBingoConfig(strategy="debit_spread", signal=_v3())
    text = signal_gate.refusal(BOT_CAS_BINGO, config)
    assert text is not None and "does not run on SENSEX" in text


def test_with_sensex_off_cas_bingo_is_held_only_by_the_gate():
    config = CasBingoConfig(
        strategy="debit_spread", signal=_v3(),
        indices={"NIFTY": CasBingoIndex(), "BSESEN": CasBingoIndex(enabled=False)},
    )
    assert signal_gate.refusal(BOT_CAS_BINGO, config) == (
        "Momentum v3 1m is not yet available to bots. closed v3")


def test_the_gate_is_asked_about_the_version_the_bot_picked():
    saved = MomentumLongScalperConfig.model_validate(
        {"signal": {"mechanism": "momentum", "duration": 15, "direction": "fade"}})
    assert signal_gate.refusal(BOT_MOMENTUM_LONG_SCALPER, saved).endswith("closed v2")


def test_every_version_is_its_own_backtest_setting():
    combos = backtest_combos.combos_for("momentum", MomentumLongScalperConfig())
    ids = [c.id for c in combos]
    # 24 signal settings, plus the premium gate at three thresholds (#75).
    assert len(ids) == len(set(ids)) == 27
    assert len([c for c in combos if c.signal is not None]) == 24
    assert {"expansion-15m-fade", "momentum-v3-1m-follow", "momentum-15m-fade",
            "momentum-v1-5m-follow"} <= set(ids)
    (saved,) = [c for c in combos if c.is_saved]
    assert saved.id == "momentum-v3-1m-follow"
    assert saved.signal == {"mechanism": "momentum", "version": 3, "duration": 1, "direction": "follow"}


def test_follow_and_fade_of_one_series_stay_adjacent():
    """The readings cache holds two series because follow and fade of one run back to back."""
    combos = [c for c in backtest_combos.combos_for("momentum", MomentumLongScalperConfig())
              if c.signal is not None]
    series = [c.config.signal.series_id("NIFTY") for c in combos]
    assert all(series[i] == series[i + 1] for i in range(0, len(series), 2))
    # The premium gate's rows (#75) sit after the saved pair, not between follow and fade.
    every = backtest_combos.combos_for("momentum", MomentumLongScalperConfig())
    saved = next(i for i, c in enumerate(every) if c.is_saved)
    assert every[saved + 1].id.endswith("-fade") and every[saved + 2].id.startswith("premium-")


def test_an_old_run_shows_under_the_version_it_replayed():
    """A run from before versions coexisted keyed Momentum by the plain id, whatever version it
    was; a v1-era run must never be shown as v2's evidence."""
    v1_era = {"versions": {"momentum": 1}, "summary": {"nifty:momentum:15m": {"calls": 7}}}
    assert _run_summary(v1_era, SeriesKey("momentum", 15, "nifty", 1)) == {"calls": 7}
    assert _run_summary(v1_era, SeriesKey("momentum", 15, "nifty", 2)) is None
    new = {"versions": {"momentum": [1, 2, 3]},
           "summary": {"nifty:momentum:v3:15m": {"calls": 3}, "nifty:momentum:15m": {"calls": 5}}}
    assert _run_summary(new, SeriesKey("momentum", 15, "nifty", 3)) == {"calls": 3}
    assert _run_summary(new, SeriesKey("momentum", 15, "nifty", 2)) == {"calls": 5}


def test_paper_evidence_survives_the_version_field():
    """Hashes taken before #72 (pinned from the commit before it). A legacy-version choice
    decides what it decided before, so it must not void a bot's paper evidence; v3 must."""
    from icici_breeze_backend.app.services.bots.scalping.evidence import material_config_hash

    v2 = {"signal": {"mechanism": "momentum", "duration": 5, "direction": "fade"}}
    expansion = {"signal": {"mechanism": "expansion", "duration": 15, "direction": "fade"}}
    assert material_config_hash(BOT_MOMENTUM_LONG_SCALPER, v2) == "3f251d46b46a2b21"
    assert material_config_hash(BOT_MOMENTUM_LONG_SCALPER, expansion) == "3b14ed4eed36efb3"
    v3 = {"signal": {**v2["signal"], "version": 3}}
    assert material_config_hash(BOT_MOMENTUM_LONG_SCALPER, v3) != "3f251d46b46a2b21"
