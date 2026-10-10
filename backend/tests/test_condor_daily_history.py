"""The condor's long history on NSE daily closes (docs/condor-daily-history-plan.md)."""
from __future__ import annotations

import datetime
import io
import zipfile

import pytest

from icici_breeze_backend.app.domain.condor import CondorSettings
from icici_breeze_backend.app.services.bots.charges import ChargesModel
from icici_breeze_backend.app.services.bots.scalping import backtest_regime as regime
from icici_breeze_backend.app.services.bots.scalping.spreads import SpreadStats
from icici_breeze_backend.app.services.condor import daily_history as dh
from icici_breeze_backend.app.services.condor.backtest import CondorReplay, DailySource
from icici_breeze_backend.app.services.condor.pricing import bs_price, years_to_expiry_close

D = datetime.date
SPREAD = SpreadStats(source="default", samples=0, median_spread_pct=0.5)


def _zip(text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("bhav.csv", text)
    return buf.getvalue()


OLD = """INSTRUMENT,SYMBOL,EXPIRY_DT,STRIKE_PR,OPTION_TYP,OPEN,HIGH,LOW,CLOSE,SETTLE_PR,CONTRACTS,VAL_INLAKH,OPEN_INT,CHG_IN_OI,TIMESTAMP,
OPTIDX,NIFTY,26-Mar-2020,7600,CE,300,400,200,350.5,7610.25,1200,1,5000,0,23-MAR-2020,
OPTIDX,NIFTY,26-Mar-2020,7700,CE,250,300,200,280,7610.25,0,0,5000,0,23-MAR-2020,
OPTIDX,BANKNIFTY,26-Mar-2020,16000,PE,1,1,1,500,16000,10,1,5,0,23-MAR-2020,
OPTIDX,NIFTY,26-Mar-2020,11000,CE,1,1,1,0.5,7610.25,10,1,5,0,23-MAR-2020,
OPTIDX,NIFTY,30-Jul-2020,7600,PE,900,900,900,950,7610.25,10,1,5,0,23-MAR-2020,
FUTIDX,NIFTY,26-Mar-2020,0,XX,7600,7700,7500,7650,7650,10,1,5,0,23-MAR-2020,
"""

UDIFF = """TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,XpryDt,FininstrmActlXpryDt,StrkPric,OptnTp,FinInstrmNm,OpnPric,HghPric,LwPric,ClsPric,LastPric,PrvsClsgPric,UndrlygPric,SttlmPric,OpnIntrst,ChngInOpnIntrst,TtlTradgVol,TtlTrfVal,TtlNbOfTxsExctd,SsnId,NewBrdLotQty,Rmks,Rsvd1,Rsvd2,Rsvd3,Rsvd4
2026-09-29,2026-09-29,FO,NSE,IDO,1,,NIFTY,,2026-09-29,2026-09-29,19500.00,CE,X,3145.00,3258.25,3082.00,3199.25,3202.60,3305.95,22716.20,22716.20,56810,-11505,522,1,173,F1,65,,,,,
2026-09-29,2026-09-29,FO,NSE,IDO,2,,NIFTY,,2026-09-29,2026-09-29,21200.00,CE,X,0.00,0.00,0.00,3038.10,0.00,3038.10,22716.20,22716.20,0,0,0,0.00,0,F1,65,,,,,
2026-09-29,2026-09-29,FO,NSE,IDO,3,,BANKNIFTY,,2026-09-29,2026-09-29,50000.00,PE,X,1,1,1,1,1,1,50000,50000,1,0,5,1,1,F1,30,,,,,
"""


def test_the_old_format_keeps_only_nifty_options_that_traded_near_the_money():
    rows = dh.parse_old(_zip(OLD), D(2020, 3, 23), 7610.25)
    assert rows == [("2020-03-23", "2020-03-26", 7600.0, "Call", 300.0, 350.5, 1200, 5000)]
    # Dropped: the 7,700 call (no trade), BANKNIFTY, the 11,000 call (beyond the band), the July
    # put (more than 100 days out) and the future. SETTLE_PR (the index on expiry day) is unused.


def test_udiff_drops_the_stale_close_of_a_contract_that_did_not_trade():
    rows, stated = dh.parse_udiff(_zip(UDIFF), D(2026, 9, 29), None)
    assert stated == 22716.20
    assert [(r[2], r[5]) for r in rows] == [(19500.0, 3199.25)]


class _Resp:
    def __init__(self, status, content=b""):
        self.status_code, self.content = status, content


def _fetcher(path, answers):
    asked = []

    def get(url):
        asked.append(url)
        for key, resp in answers.items():
            if key in url:
                return resp
        return _Resp(404)

    return dh.DailyFetcher(path=path, get=get, sleep=lambda s: None, log=lambda line: None), asked


INDEX = "Index Name,Index Date,Open Index Value,High Index Value,Low Index Value,Closing Index Value\nNifty 50,23-03-2020,8000,8000,7500,7610.25\n"


def test_a_session_is_stored_once_and_a_holiday_is_recognised_by_two_404s(tmp_path):
    path = str(tmp_path / "cache.sqlite3")
    dh.ensure_tables(path)
    fetcher, asked = _fetcher(path, {
        "ind_close_all_23032020": _Resp(200, INDEX.encode()),
        "fo23MAR2020bhav": _Resp(200, _zip(OLD)),
    })
    assert fetcher.fetch_day(D(2020, 3, 23)) == dh.OK
    assert fetcher.fetch_day(D(2020, 3, 10)) == dh.NO_FILE  # Holi: both files 404
    assert dh.index_closes(D(2020, 3, 1), D(2020, 3, 31), path) == {D(2020, 3, 23): 7610.25}
    # Neither is asked for again; a network error is not recorded, so it is retried.
    assert fetcher.missing(D(2020, 3, 9), D(2020, 3, 24)) == [
        D(2020, 3, 9), D(2020, 3, 11), D(2020, 3, 12), D(2020, 3, 13), D(2020, 3, 16), D(2020, 3, 17),
        D(2020, 3, 18), D(2020, 3, 19), D(2020, 3, 20), D(2020, 3, 24)]
    broken, _ = _fetcher(path, {"ind_close_all": _Resp(0)})
    assert broken.fetch_day(D(2020, 3, 24)) == dh.ERROR
    assert D(2020, 3, 24) in broken.missing(D(2020, 3, 24), D(2020, 3, 24))


def test_the_old_format_is_asked_for_before_july_2024_and_udiff_after(tmp_path):
    path = str(tmp_path / "cache.sqlite3")
    dh.ensure_tables(path)
    fetcher, asked = _fetcher(path, {})
    fetcher.fetch_day(D(2024, 7, 4))
    fetcher.fetch_day(D(2024, 7, 8))
    assert any("historical/DERIVATIVES/2024/JUL/fo04JUL2024bhav" in u for u in asked)
    assert any("BhavCopy_NSE_FO_0_0_0_20240708" in u for u in asked)
    assert not any("fo08JUL2024bhav" in u for u in asked)


# --- the replay on daily closes -------------------------------------------------------------


def _last_thursday(year, month):
    d = D(year, month + 1, 1) - datetime.timedelta(days=1) if month < 12 else D(year, 12, 31)
    while d.weekday() != 3:
        d -= datetime.timedelta(days=1)
    return d


def _seed(path, start, end, spot=12_000.0, sigma=0.16):
    """Flat NIFTY at 2020's level, monthlies expiring on (2020's) Thursdays, every strike traded."""
    dh.ensure_tables(path)
    fetcher = dh.DailyFetcher(path=path, log=lambda line: None)
    expiries = [_last_thursday(2020, m) for m in range(1, 10)]
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
                        price = round(max(0.05, bs_price(right, spot, float(k), t, sigma)), 2)
                        rows.append((day.isoformat(), e.isoformat(), float(k), right, price, price, 100, 1000))
            fetcher._record(day, dh.OK, (spot, spot), rows)  # noqa: SLF001
        day += datetime.timedelta(days=1)


def test_a_condor_replays_on_daily_closes_at_todays_notional(tmp_path):
    path = str(tmp_path / "cache.sqlite3")
    start, end = D(2020, 1, 1), D(2020, 5, 29)
    _seed(path, start, end)
    source = DailySource(start, end, reference_spot=24_000.0, path=path)
    replay = CondorReplay(
        CondorSettings(margin_ceiling_inr=60_00_000), start, end, lots_per_tranche=2,
        exit_action="time_roll", source=source, charges=ChargesModel(), spread=SPREAD, holidays=set(),
        checks=("eod",),
    )
    assert replay.run() == []
    s = replay.summary()
    assert s["complete"] and s["campaigns"]
    events = replay.events
    assert events and {e["check"] for e in events} == {"eod"}, "close-only: no 10:30 check"
    # 2020's monthlies expired on Thursdays; the replay took them from the data, not today's rule.
    assert {datetime.date.fromisoformat(e["expiry"]).weekday() for e in events if e["expiry"]} == {3}
    # Sized to today's money: NIFTY 24,000 now against 12,000 then doubles each lot.
    entry = next(e for e in events if e["action"] == "enter")
    lot = regime.lot_size_for("NIFTY", start)
    assert {o["qty"] for o in entry["orders"]} == {2 * round(lot * 2.0)}


def test_a_day_a_contract_did_not_trade_leaves_it_unquoted_not_unlisted(tmp_path):
    path = str(tmp_path / "cache.sqlite3")
    _seed(path, D(2020, 1, 1), D(2020, 1, 3))
    source = DailySource(D(2020, 1, 1), D(2020, 1, 3), reference_spot=24_000.0, path=path)
    contract = (D(2020, 1, 30), 12_000.0, "Call")
    assert source.option(contract, datetime.datetime(2020, 1, 2, 15, 31))[0] == "ok"
    assert source.option(contract, datetime.datetime(2020, 1, 4, 15, 31)) == ("stale", None)
    assert source.trading_days(D(2020, 1, 1), D(2020, 1, 31)) == [D(2020, 1, 1), D(2020, 1, 2), D(2020, 1, 3)]
    assert source.listed_expiries(D(2020, 1, 2))[0] == D(2020, 1, 30)


# --- the job ------------------------------------------------------------------------------


def test_daily_prices_reach_back_to_2020_and_icici_prices_do_not(monkeypatch):
    from icici_breeze_backend.app.services.bots import backtest_jobs as jobs
    from icici_breeze_backend.app.services.condor import backtest_job

    for name in ("ensure_store", "refuse_if_storage_blocked", "_memory_check"):
        monkeypatch.setattr(jobs, name, lambda *a, **k: None)
    backtest_job.check_startable(D(2020, 1, 1), D(2020, 6, 1), 1, prices="nse_daily")
    with pytest.raises(ValueError, match="NSE daily-price history starts 01 Jan 2020"):
        backtest_job.check_startable(D(2019, 12, 1), D(2020, 6, 1), 1, prices="nse_daily")
    with pytest.raises(ValueError, match="ICICI's option history"):
        backtest_job.check_startable(D(2020, 1, 1), D(2020, 6, 1), 1, prices="icici")


def test_a_daily_run_replays_every_combination_on_stored_prices(tmp_path, monkeypatch):
    from icici_breeze_backend.app.services.bots import backtest_jobs as jobs
    from icici_breeze_backend.app.services.bots.scalping import backtest_store as store
    from icici_breeze_backend.app.services.condor import backtest_job

    path = str(tmp_path / "cache.sqlite3")
    monkeypatch.setattr(store, "db_path", lambda: path)
    monkeypatch.setattr(store, "save_run", lambda run, path=None: None)
    monkeypatch.setattr(jobs, "market_hours_reason", lambda now=None: "The market is open.")
    monkeypatch.setattr(jobs, "_update", lambda **k: None)
    monkeypatch.setattr(jobs, "_log", lambda line: None)
    monkeypatch.setattr(jobs, "_memory_check", lambda *a, **k: None)
    monkeypatch.setattr(backtest_job, "tradeable_band", lambda settings, today: (None, "No band."))
    start, end = D(2020, 1, 1), D(2020, 4, 30)
    _seed(path, start, end)
    settings = CondorSettings(margin_ceiling_inr=60_00_000)
    combos = backtest_job.backtest_combos.combos_for(settings, "time_roll")
    run, notes = {}, []
    backtest_job._run_daily("u1", settings, start, end, combos, 2, notes, run,  # noqa: SLF001
                            datetime.datetime(2026, 10, 9, 10, 0))
    summary = run["summary"]
    assert summary["prices"] == "nse_daily" and len(summary["comparison"]) == len(combos)
    # Market hours: nothing downloaded, the stored sessions replayed, and every note says how.
    assert any("Nothing downloaded" in n for n in notes)
    assert any("close-only" in n or "at the close" in n for n in notes)
    assert any("sized to NIFTY 12,000" in n for n in notes)


def test_deleting_sessions_on_the_storage_screen_forgets_they_were_fetched(tmp_path):
    from icici_breeze_backend.app.services.storage import elements

    path = str(tmp_path / "cache.sqlite3")
    _seed(path, D(2020, 1, 1), D(2020, 1, 10))
    rng = elements.DateRange(D(2020, 1, 1), D(2020, 1, 3))
    n, _note = elements._delete_nse_daily(rng, path)  # noqa: SLF001
    assert n == 3
    assert dh.sessions(D(2020, 1, 1), D(2020, 1, 10), path)[0] == D(2020, 1, 6)
    assert dh.DailyFetcher(path=path).missing(D(2020, 1, 1), D(2020, 1, 10)) == [D(2020, 1, 1), D(2020, 1, 2), D(2020, 1, 3)]


def test_a_wing_that_did_not_trade_is_priced_off_the_sessions_smile(tmp_path):
    """The user's choice (2026-10-09): live, the bot buys a far wing at its quote, so a replay
    that cannot price an untraded wing enters tranches late. On a flat 16% smile the estimate is
    the price the contract would have traded at."""
    import sqlite3

    path = str(tmp_path / "cache.sqlite3")
    _seed(path, D(2020, 1, 1), D(2020, 1, 3))
    wing = (D(2020, 2, 27), 10_500.0, "Put")
    traded = DailySource(D(2020, 1, 1), D(2020, 1, 3), reference_spot=24_000.0, path=path)
    at = datetime.datetime(2020, 1, 2, 15, 31)
    status, real = traded.option(wing, at)
    assert status == "ok" and traded.estimated == set()
    with sqlite3.connect(path) as conn:
        conn.execute("DELETE FROM nse_daily_options WHERE session = '2020-01-02' AND strike = 10500")
    source = DailySource(D(2020, 1, 1), D(2020, 1, 3), reference_spot=24_000.0, path=path)
    status, estimate = source.option(wing, at)
    assert status == "ok" and estimate == pytest.approx(real, rel=0.03)
    assert source.estimated == {(D(2020, 1, 2), wing)}


def test_no_smile_no_estimate(tmp_path):
    import sqlite3

    path = str(tmp_path / "cache.sqlite3")
    _seed(path, D(2020, 1, 1), D(2020, 1, 3))
    with sqlite3.connect(path) as conn:  # leave fewer than three traded contracts on the expiry
        conn.execute("DELETE FROM nse_daily_options WHERE session = '2020-01-02' AND expiry = '2020-02-27' "
                     "AND strike NOT IN (12000, 12050)")
    source = DailySource(D(2020, 1, 1), D(2020, 1, 3), reference_spot=24_000.0, path=path)
    assert source.option((D(2020, 2, 27), 10_500.0, "Put"), datetime.datetime(2020, 1, 2, 15, 31)) == ("stale", None)
