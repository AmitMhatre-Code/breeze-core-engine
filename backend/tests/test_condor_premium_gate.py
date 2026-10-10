"""The premium gate on the Dynamic Iron Condor's tranche entries (#78)."""
from __future__ import annotations

import datetime
from types import SimpleNamespace

from icici_breeze_backend.app.domain.bots import PremiumGateConfig, ReasonCode
from icici_breeze_backend.app.domain.condor import CondorSettings
from icici_breeze_backend.app.services.bots.charges import ChargesModel
from icici_breeze_backend.app.services.bots.scalping.spreads import SpreadStats
from icici_breeze_backend.app.services.condor import daily_history as dh
from icici_breeze_backend.app.services.condor.backtest import CondorReplay, DailySource
from icici_breeze_backend.app.services.condor.bot import settings_hash
from icici_breeze_backend.app.services.condor.engine import premium_refusal
from icici_breeze_backend.app.services.condor.model import MarketSnapshot
from icici_breeze_backend.app.services.condor.pricing import bs_price, years_to_expiry_close

D = datetime.date
SPREAD = SpreadStats(source="default", samples=0, median_spread_pct=0.5)
GATED = CondorSettings(margin_ceiling_inr=60_00_000, premium_gate=PremiumGateConfig(enabled=True, threshold=1.0))


def _market(forecast, why=None):
    return MarketSnapshot(now=datetime.datetime(2026, 3, 2, 10, 30), spot=24_000.0, spot_live=True,
                          feeds_ok=True, chain=(), forecast_variance=forecast, forecast_reason=why)


def test_a_tranche_waits_while_premium_is_not_rich():
    model = SimpleNamespace(atm_sigma=0.16, t=45 / 365)
    implied = 0.16 ** 2 * 45 / 365
    assert premium_refusal(model, _market(implied * 0.81), GATED) is None   # 1.11x: rich, enters
    code, text = premium_refusal(model, _market(implied * 1.5), GATED)     # 0.82x: waits
    assert code == ReasonCode.PREMIUM_NOT_RICH and "0.82x the forecast move" in text


def test_no_forecast_or_no_atm_volatility_is_no_tranche():
    assert premium_refusal(SimpleNamespace(atm_sigma=0.16, t=0.1), _market(None, "no_history"), GATED)[0] \
        == ReasonCode.PREMIUM_UNREADABLE
    assert premium_refusal(SimpleNamespace(atm_sigma=None, t=0.1), _market(0.001), GATED)[0] \
        == ReasonCode.PREMIUM_UNREADABLE


def test_an_off_gate_never_reads_anything():
    off = CondorSettings(margin_ceiling_inr=60_00_000)
    assert premium_refusal(SimpleNamespace(atm_sigma=None, t=0.1), _market(None), off) is None


def test_an_off_gate_keeps_every_piece_of_evidence_earned_before_it_existed():
    """Runs, Simulation cycles and approvals stored before #78 have no `premium_gate`."""
    off = CondorSettings(margin_ceiling_inr=60_00_000).model_dump(mode="json")
    before = {k: v for k, v in off.items() if k != "premium_gate"}
    assert settings_hash(off, "time_roll", 2) == settings_hash(before, "time_roll", 2)
    assert settings_hash(GATED.model_dump(mode="json"), "time_roll", 2) != settings_hash(before, "time_roll", 2)


# --- replayed ------------------------------------------------------------------------------


def _last_thursday(year, month):
    d = (D(year, month + 1, 1) if month < 12 else D(year + 1, 1, 1)) - datetime.timedelta(days=1)
    while d.weekday() != 3:
        d -= datetime.timedelta(days=1)
    return d


def _seed(path, start, end, *, swing, spot=12_000.0, sigma=0.16):
    """Options priced at a 16% volatility every session; the index opens `swing` below its close
    and closes at 12,000, so it realises about sqrt(2) x swing a session, whatever the options say."""
    dh.ensure_tables(path)
    fetcher = dh.DailyFetcher(path=path, log=lambda line: None)
    expiries = [_last_thursday(y, m) for y, m in [(2019, 12)] + [(2020, m) for m in range(1, 10)]]
    day = start
    while day <= end:
        if day.weekday() < 5:
            rows = []
            now = datetime.datetime.combine(day, datetime.time(15, 30))
            for e in expiries:
                if not 0 <= (e - day).days <= dh.MAX_DTE:
                    continue
                t = years_to_expiry_close(e, now)
                for k in range(int(spot * 0.82) // 50 * 50, int(spot * 1.18), 50):
                    for right in ("Call", "Put"):
                        p = round(max(0.05, bs_price(right, spot, float(k), t, sigma)), 2)
                        rows.append((day.isoformat(), e.isoformat(), float(k), right, p, p, 100, 1000))
            fetcher._record(day, dh.OK, (spot * (1 - swing), spot), rows)  # noqa: SLF001
        day += datetime.timedelta(days=1)


def _replay(path, settings):
    start, end = D(2020, 1, 1), D(2020, 4, 30)
    source = DailySource(start, end, reference_spot=24_000.0, path=path)
    replay = CondorReplay(settings, start, end, lots_per_tranche=1, exit_action="close", source=source,
                          charges=ChargesModel(), spread=SPREAD, holidays=set(), checks=("eod",))
    assert replay.run() == []
    return replay


def test_a_calm_index_under_a_16_percent_smile_lets_tranches_in(tmp_path):
    path = str(tmp_path / "c.sqlite3")
    _seed(path, D(2019, 11, 1), D(2020, 4, 30), swing=0.003)  # ~0.4% a session: options rich
    replay = _replay(path, GATED)
    assert any(e["action"] == "enter" for e in replay.events)
    assert "premium_not_rich" not in replay.summary()["skipped"]


def test_a_wild_index_keeps_every_tranche_out_and_says_why(tmp_path):
    path = str(tmp_path / "w.sqlite3")
    _seed(path, D(2019, 11, 1), D(2020, 4, 30), swing=0.012)  # ~1.7% a session: options cheap
    gated = _replay(path, GATED)
    assert not any(e["action"] == "enter" for e in gated.events)
    ungated = _replay(path, CondorSettings(margin_ceiling_inr=60_00_000))
    assert any(e["action"] == "enter" for e in ungated.events), "the same days trade with the gate off"


def test_a_refused_tranche_is_counted_and_logged(tmp_path):
    path = str(tmp_path / "w.sqlite3")
    _seed(path, D(2019, 11, 1), D(2020, 4, 30), swing=0.012)
    replay = _replay(path, GATED)
    assert replay.summary()["skipped"].get("premium_not_rich", 0) > 0
    assert any(e["reason"] == "premium_not_rich" and e["action"] == "skipped" for e in replay.events)
