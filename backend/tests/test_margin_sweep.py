"""Calibration sweep (pilot): NSE market context, the case grid, and resumable runs."""
from __future__ import annotations

import gzip
import sqlite3
from unittest.mock import MagicMock

import pytest

from icici_breeze_backend.app.services.margin_harness import market_context as mc
from icici_breeze_backend.app.services.margin_harness import runner, store, sweep

# Real rows from NSE's files for 25-Sep-2026.
INDICES = (
    "Index Name,Index Date,Open Index Value,High Index Value,Low Index Value,Closing Index Value,"
    "Points Change,Change(%),Volume,Turnover (Rs. Cr.),P/E,P/B,Div Yield\n"
    "Nifty 50,25-09-2026,23035,23162.7,23020.95,23140.5,77.4,.34,242720711,18949.25,19.56,2.8,1.22\n"
    "India VIX,25-09-2026,12.6875,12.835,11.77,12.16,-0.53,-4.16,-,-,-,-,-\n"
)
FOVOLT = (
    "Date, Symbol, A, B, C, D, E, F, G, H, I, J, K, L, M, N\n"
    "25-Sep-26,TCS,2082.31,2086.61, -0.00206289,  0.01743508,  0.01739199,  0.33227354,2086.7,2079.8,"
    "  0.00331214,  0.01706625,  0.01702523,  0.32526662,  0.01739199,  0.33227354\n"
    "25-Sep-26,BANKNIFTY,55580.4,55000,0.01,0.01,0.0102,0.196,55600,55000,0.01,0.01,0.0101,0.19,0.0102,0.19607839\n"
)
VAR = (
    "10,25092026,0.00,06,0018635\n"
    "20,0ABCL31,N0,INE674K07150,0.00,0.00,10.00,0.00,0.00,10.00\n"
    "20,TCS,EQ,INE467B01029,11.00,0.00,11.00,3.50,0.00,14.50\n"
)
BAN = "Securities in Ban For Trade Date 25-SEP-2026:\n1,KAYNES\n2,SAIL\n"


def test_parsers_read_nse_files():
    assert mc.parse_india_vix(INDICES)["close"] == 12.16
    vol = mc.parse_volatility(FOVOLT)
    assert vol["TCS"]["annual_vol"] == pytest.approx(0.33227354)
    var = mc.parse_var(VAR)
    assert set(var) == {"TCS"}  # only EQ series
    assert var["TCS"]["applicable_margin_pct"] == 14.5 and var["TCS"]["elm_pct"] == 3.5
    assert mc.parse_ban(BAN) == ["KAYNES", "SAIL"]


def test_features_match_any_registry_spelling():
    ctx = {
        "india_vix": {"close": 12.16},
        "volatility": mc.parse_volatility(FOVOLT),
        "var": mc.parse_var(VAR),
        "fo_ban": ["SAIL"],
    }
    # Bank Nifty is NIFTY BANK in the registry but BANKNIFTY in NSE's volatility file.
    bank = mc.features_for(ctx, ("CNXBAN", "NIFTY BANK", "BANKNIFTY"))
    assert bank["annual_vol"] == pytest.approx(0.19607839) and bank["exchange_symbol"] == "BANKNIFTY"
    tcs = mc.features_for(ctx, ("TCS",))
    assert tcs["applicable_margin_pct"] == 14.5 and tcs["india_vix_close"] == 12.16
    assert mc.features_for(ctx, ("SAIL",))["in_fo_ban"] is True


def _strikes(spot: float, step: float, span: float = 0.4) -> dict[str, list[float]]:
    lo, hi = spot * (1 - span), spot * (1 + span)
    ladder = [round(lo + i * step, 2) for i in range(int((hi - lo) / step) + 1)]
    return {"CE": ladder, "PE": ladder}


def test_core_grid_covers_itm_atm_otm_and_the_deep_line():
    specs = {s[0]: s for s in sweep._core_specs(_strikes(23140.5, 50), 23140.5, True)}
    for right in ("call", "put"):
        for tag in ("itm5", "otm0", "otm5", "otm10", "otm12"):
            assert f"short_{right}_{tag}" in specs
    assert {"bull_put_spread", "bear_call_spread", "short_strangle", "iron_condor"} <= set(specs)
    # 12% OTM for an index sits past the 10% deep line; 32% for a stock past 30%.
    stock = {s[0] for s in sweep._core_specs(_strikes(1224.6, 10, 0.5), 1224.6, False)}
    assert "short_call_otm32" in stock and "short_put_otm32" in stock


def test_a_strike_too_far_from_its_point_is_skipped_not_mislabelled():
    sparse = {"CE": [1000.0, 1300.0], "PE": [1000.0]}
    # 1300 is 6.2% OTM: close enough to stand for 5%, too far (3.8 points) to be "10% OTM".
    assert sweep._strike_at(sparse, "Call", 1224.6, 0.05) == 1300.0
    assert sweep._strike_at(sparse, "Call", 1224.6, 0.10) is None


def test_dense_ladder_skips_what_the_core_already_priced():
    strikes = _strikes(23140.5, 50)
    core = sweep._core_specs(strikes, 23140.5, True)
    seen = {(s[2][0][1], s[2][0][0]) for s in core if len(s[2]) == 1}
    dense = sweep._dense_specs(strikes, 23140.5, set(seen))
    dense_keys = {(s[2][0][1], s[2][0][0]) for s in dense}
    assert dense and not (dense_keys & seen)


def test_quantity_cases_scale_lots():
    specs = sweep._quantity_specs(_strikes(23140.5, 50), 23140.5)
    assert [s[2][0][3] for s in specs] == [10, 10, 50, 50]


# --- resumable runs -----------------------------------------------------------------------------


@pytest.fixture
def harness_db(tmp_path, monkeypatch):
    monkeypatch.setattr(store.cfg, "DATA_PATH", str(tmp_path) + "/")
    monkeypatch.setattr(store.cfg, "USERS_DB", "users.sqlite3")
    store.ensure_margin_harness_tables()
    return tmp_path / "users.sqlite3"


def _cases(n: int):
    from icici_breeze_backend.app.services.margin_harness.cases import CaseLeg, HarnessCase

    return [
        HarnessCase(
            id=f"C{i}",
            label="t",
            structure="t",
            source="sweep_core",
            stock_code="NIFTY",
            exchange_code="NFO",
            expiry_date="29-Sep-2099",
            is_index=True,
            is_expiry_day=False,
            lot_size=65,
            spot_price=23140.5,
            spot_source="t",
            som_rate=0.0,
            legs=[CaseLeg("NIFTY", "NFO", "29-Sep-2099", 23150.0, "Call", "Sell", 65)],
            features={"grid": "core"},
        )
        for i in range(n)
    ]


def _start(run_id: str, cases, *, max_calls=None, mode=runner.MODE_SWEEP):
    meta = {
        "mode": mode,
        "max_calls": max_calls,
        "icici_addon": {"available": False},
        "market_context": {"india_vix": {"close": 12.16}},
        "cases": [c.as_dict() for c in cases],
    }
    store.create_run(run_id, "u1", "2026-09-27T10:00:00+05:30", len(cases), mode=mode, meta=meta)
    return meta


@pytest.fixture
def fake_eval(monkeypatch):
    calls: list[str] = []

    def _eval(case, icici, addon_rates=None):
        calls.append(case.id)
        return {"case": case.as_dict(), "icici": icici, "combinations": [], "app_margin": {}}

    monkeypatch.setattr(runner, "_evaluate_case", _eval)
    return calls


def _breeze():
    b = MagicMock()
    b.margin_calculator.return_value = {"Status": 200, "Success": {"span_margin_required": "100"}}
    return b


def test_a_run_pauses_at_its_call_cap_and_resumes_where_it_left_off(harness_db, fake_eval):
    cases = _cases(5)
    meta = _start("r1", cases, max_calls=2)
    out = runner._execute("r1", "u1", _breeze(), cases, meta, started_at="t")
    assert out["status"] == "paused" and store.get_run("r1")["status"] == "paused"
    assert store.result_count("r1") == 2

    run = store.get_run("r1")
    meta["max_calls"] = None
    out = runner._execute(
        "r1", "u1", _breeze(), runner._cases_from_meta(run["meta"]), meta, started_at="t",
        priced=run["priced_count"], broker_calls=run["broker_calls"],
    )
    assert out["ok"] and fake_eval == ["C0", "C1", "C2", "C3", "C4"]
    finished = store.get_run("r1")
    assert finished["status"] == "completed" and finished["broker_calls"] == 5
    assert store.result_count("r1") == 0  # folded into the payload


def test_a_sweep_payload_is_stored_and_served_compressed(harness_db, fake_eval):
    cases = _cases(2)
    meta = _start("r2", cases)
    runner._execute("r2", "u1", _breeze(), cases, meta, started_at="t")
    with sqlite3.connect(harness_db) as conn:
        text, gz = conn.execute(
            "SELECT payload_json, payload_gz FROM margin_harness_runs WHERE id = 'r2'"
        ).fetchone()
    assert text is None and gzip.decompress(gz)
    payload = store.get_run_payload("r2")
    assert payload["mode"] == "sweep" and payload["market_context"]["india_vix"]["close"] == 12.16
    assert len(payload["results"]) == 2


def test_stop_leaves_the_run_resumable(harness_db, fake_eval):
    cases = _cases(3)
    meta = _start("r3", cases)
    runner._cancel.set()
    b = _breeze()
    # _execute clears the flag on entry, so set it from the first broker call instead.
    b.margin_calculator.side_effect = lambda *a, **k: (runner._cancel.set(), {"Status": 200, "Success": {"span_margin_required": "1"}})[1]
    out = runner._execute("r3", "u1", b, cases, meta, started_at="t")
    assert out["status"] == "stopped" and store.result_count("r3") == 1
    assert [r for r in store.list_runs() if r["id"] == "r3"][0]["resumable"] is True


def test_a_run_left_running_by_a_restart_is_marked_interrupted(harness_db):
    _start("r4", _cases(1))
    assert store.reap_interrupted_runs() == 1
    run = [r for r in store.list_runs() if r["id"] == "r4"][0]
    assert run["status"] == "interrupted" and run["resumable"] is True


def test_harness_calls_run_in_the_users_icici_scope(harness_db, fake_eval):
    """Sweep 6350d21b: with no user in scope the pacer skipped its minute window and lock,
    fired 547 calls back to back, and ICICI refused 105."""
    from icici_breeze_backend.app.services.icici_api_pacing import GlobalIciciApiLimiter

    seen: list[str | None] = []
    b = MagicMock()

    def _call(*a, **k):
        seen.append(GlobalIciciApiLimiter.resolve_user_id(None))
        return {"Status": 200, "Success": {"span_margin_required": "1"}}

    b.margin_calculator.side_effect = _call
    cases = _cases(2)
    runner._execute("r5", "u1", b, cases, _start("r5", cases), started_at="t")
    assert seen == ["u1", "u1"]


def test_a_throttled_case_is_retried_not_recorded_as_failed(harness_db, fake_eval, monkeypatch):
    monkeypatch.setattr(runner, "_THROTTLE_RETRY_WAIT_SEC", 0.0)
    b = MagicMock()
    b.margin_calculator.side_effect = [
        {"Status": 5, "Error": "Limit exceed: API call per minute:Try after some time"},
        {"Status": 200, "Success": {"span_margin_required": "1"}},
    ]
    cases = _cases(1)
    runner._execute("r6", "u1", b, cases, _start("r6", cases), started_at="t")
    run = store.get_run("r6")
    assert run["priced_count"] == 1 and run["failed_count"] == 0 and run["broker_calls"] == 2
