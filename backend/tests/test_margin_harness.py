"""Tests for the SPAN engine's new terms and the margin comparison harness."""
from __future__ import annotations

import sqlite3

import pytest

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.services.margin_harness import runner, store
from icici_breeze_backend.app.services.margin_harness.methods import (
    ELM_METHODS,
    SPAN_METHODS,
    compute_elm,
    compute_span_variant,
    method_catalog,
)
from icici_breeze_backend.app.services.reference_data.span_portfolio_scan import (
    SpanLeg,
    compute_net_option_value_from_file,
    compute_portfolio_span_margin,
    compute_short_option_minimum,
)

# One short call, one long call. Risk arrays are per-unit and positive = loss for a long.
_CONTRACTS = {
    "24000:CE": {
        "margin_per_lot": 75000.0,
        "lot_size": 75,
        "risk_array": [100.0, -100.0, 500.0, -500.0],
        "settle_price": 120.0,
    },
    "24500:CE": {
        "margin_per_lot": 30000.0,
        "lot_size": 75,
        "risk_array": [40.0, -40.0, 200.0, -200.0],
        "settle_price": 45.0,
    },
}


def _leg(strike: float, side: str, qty: int = 75) -> SpanLeg:
    return SpanLeg(strike=strike, right="Call", side=side, quantity=qty)


def test_file_nov_is_the_exchange_premium_not_a_model_price():
    nov, warnings = compute_net_option_value_from_file(_CONTRACTS, [_leg(24000, "Sell")])
    assert warnings == []
    # Short option: NOV is negative -- the premium is owed, which raises the requirement.
    assert nov == pytest.approx(-120.0 * 75)


def test_file_nov_nets_long_against_short():
    nov, _ = compute_net_option_value_from_file(
        _CONTRACTS, [_leg(24000, "Sell"), _leg(24500, "Buy")]
    )
    assert nov == pytest.approx((45.0 - 120.0) * 75)


def test_file_nov_declines_rather_than_guessing_when_the_premium_is_missing():
    contracts = {"24000:CE": {**_CONTRACTS["24000:CE"], "settle_price": None}}
    nov, warnings = compute_net_option_value_from_file(contracts, [_leg(24000, "Sell")])
    assert nov is None
    assert any("Settlement premium missing" in w for w in warnings)


def test_a_long_option_needs_no_span_once_nov_is_subtracted():
    """The canonical SPAN property: a long option's risk is capped at the premium it paid, so
    the requirement nets to zero. Getting this wrong over-margins every hedge."""
    out = compute_portfolio_span_margin(_CONTRACTS, [_leg(24500, "Buy")])
    assert out["found"] is True
    assert out["net_option_value_source"] == "span_file"
    # Worst long scenario is 200/unit; the premium paid is 45/unit, so the charge is the
    # difference, not the full scan.
    assert out["span_margin_required"] == pytest.approx((200.0 - 45.0) * 75)


def test_short_option_span_is_raised_by_the_premium_owed():
    out = compute_portfolio_span_margin(_CONTRACTS, [_leg(24000, "Sell")])
    # The short's worst scenario is the negation of the long's best (-500 -> +500/unit), and
    # NOV is negative for a short, so the premium owed adds to the charge rather than reducing it.
    assert out["span_margin_required"] == pytest.approx((500.0 + 120.0) * 75)


def test_black_scholes_is_only_a_fallback():
    contracts = {"24000:CE": {**_CONTRACTS["24000:CE"], "settle_price": None}}
    out = compute_portfolio_span_margin(
        contracts, [_leg(24000, "Sell")], spot=24000.0, time_years=0.05
    )
    assert out["net_option_value_source"] == "black_scholes"
    assert any("Black-Scholes" in w for w in out["warnings"])


def test_short_option_minimum_floors_the_scan():
    legs = [_leg(24500, "Sell")]
    assert compute_short_option_minimum(legs, 0.0) == 0.0
    assert compute_short_option_minimum(legs, 10.0) == pytest.approx(750.0)
    # A long leg contributes nothing to the floor.
    assert compute_short_option_minimum([_leg(24500, "Buy")], 10.0) == 0.0

    out = compute_portfolio_span_margin(_CONTRACTS, legs, som_rate=1000.0)
    # The floor (1000 x 75) dominates the 200/unit scan, so it sets the charge.
    assert out["short_option_minimum"] == pytest.approx(75000.0)
    assert out["span_margin_required"] == pytest.approx(75000.0 - (-45.0 * 75))


def test_som_rate_of_zero_leaves_the_scan_untouched():
    """Every NSCCL/ICCL file seen so far publishes 0, so this is the normal path."""
    legs = [_leg(24500, "Sell")]
    with_som = compute_portfolio_span_margin(_CONTRACTS, legs, som_rate=0.0)
    without = compute_portfolio_span_margin(_CONTRACTS, legs)
    assert with_som["span_margin_required"] == without["span_margin_required"]


# --- harness method matrix -------------------------------------------------------------


def test_every_span_method_produces_a_figure_for_a_normal_case():
    legs = [_leg(24000, "Sell")]
    for method in SPAN_METHODS:
        out = compute_span_variant(
            method, _CONTRACTS, legs, spot=24000.0, time_years=0.05, som_rate=0.0
        )
        assert out["found"] is True, method.id
        assert out["span_margin"] >= 0


def test_span_methods_disagree_in_the_way_the_harness_exists_to_measure():
    legs = [_leg(24000, "Sell")]
    results = {
        m.id: compute_span_variant(
            m, _CONTRACTS, legs, spot=24000.0, time_years=0.05, som_rate=0.0
        )["span_margin"]
        for m in SPAN_METHODS
    }
    # Scanning alone under-charges a short: it ignores the premium owed.
    assert results["scan_only"] < results["scan_minus_nov_file"]
    # The modelled premium is not the exchange's premium, which is the whole point.
    assert results["scan_minus_nov_bs"] != results["scan_minus_nov_file"]


def test_elm_models_separate_on_a_stock_short():
    legs = [_leg(2400, "Sell", qty=225)]
    by_id = {
        m.id: compute_elm(m, legs, spot=2400.0, is_index=False, is_expiry_day=False)
        for m in ELM_METHODS
    }
    assert by_id["none"] == 0.0
    # The Portfolio model charges index shorts only, so a stock short gets nothing from it --
    # the single largest disagreement between the two models this codebase carries.
    assert by_id["portfolio_flat_index_only"] == 0.0
    assert by_id["strategy_builder_tiered"] == pytest.approx(0.05 * 2400.0 * 225)
    assert by_id["exchange_prescribed"] == pytest.approx(0.035 * 2400.0 * 225)


def test_elm_deep_otm_step_up_applies_only_to_the_tiered_model():
    deep = [_leg(30000, "Sell")]  # 25% OTM against a 24000 spot
    tiered = next(m for m in ELM_METHODS if m.id == "strategy_builder_tiered")
    flat = next(m for m in ELM_METHODS if m.id == "exchange_prescribed")
    assert compute_elm(tiered, deep, spot=24000.0, is_index=True, is_expiry_day=False) == (
        pytest.approx(cfg.ELM_INDEX_DEEP_OTM * 24000.0 * 75)
    )
    assert compute_elm(flat, deep, spot=24000.0, is_index=True, is_expiry_day=False) == (
        pytest.approx(0.02 * 24000.0 * 75)
    )


def test_expiry_day_waiver_is_what_the_two_exchange_models_differ_on():
    """Running the harness on an expiry day is the only way to tell these apart, which is why
    both are in the matrix."""
    legs = [_leg(24000, "Sell")]
    waived = next(m for m in ELM_METHODS if m.id == "exchange_prescribed")
    charged = next(m for m in ELM_METHODS if m.id == "exchange_prescribed_no_expiry_waiver")
    assert compute_elm(waived, legs, spot=24000.0, is_index=True, is_expiry_day=True) == 0.0
    assert compute_elm(charged, legs, spot=24000.0, is_index=True, is_expiry_day=True) > 0.0
    # On any other day they agree, so a normal run cannot distinguish them.
    assert compute_elm(
        waived, legs, spot=24000.0, is_index=True, is_expiry_day=False
    ) == compute_elm(charged, legs, spot=24000.0, is_index=True, is_expiry_day=False)


def test_elm_is_a_percentage_of_underlying_notional_not_premium():
    legs = [_leg(24000, "Sell")]
    method = next(m for m in ELM_METHODS if m.id == "exchange_prescribed")
    elm = compute_elm(method, legs, spot=24000.0, is_index=True, is_expiry_day=False)
    assert elm == pytest.approx(0.02 * 24000.0 * 75)


def test_method_catalog_is_embedded_and_complete():
    catalog = method_catalog()
    assert {m["id"] for m in catalog["span_methods"]} == {m.id for m in SPAN_METHODS}
    assert {m["id"] for m in catalog["elm_methods"]} == {m.id for m in ELM_METHODS}
    # Every entry has to be self-describing: an export is read long after the code moved on.
    for entry in catalog["span_methods"]:
        assert entry["nov_source"] in ("none", "span_file", "black_scholes")
        assert entry["notes"]
    for entry in catalog["elm_methods"]:
        assert entry["notional_basis"] == "underlying_spot"
        assert entry["notes"]


# --- broker response parsing ------------------------------------------------------------


def test_icici_figures_capture_every_field_not_just_span():
    """The app normally reads only span_margin_required; the harness must not, since
    non_span_margin_required is the field that answers whether ICICI breaks out exposure."""
    raw = {
        "Status": 200,
        "Success": {
            "span_margin_required": "141673.00",
            "non_span_margin_required": "31197.00",
            "order_value": "1559850.00",
            "order_margin": "0",
            "block_trade_margin": "0",
            "trade_margin": None,
        },
    }
    out = runner._icici_figures(raw)
    assert out["ok"] is True
    assert out["span_margin_required"] == pytest.approx(141673.0)
    assert out["non_span_margin_required"] == pytest.approx(31197.0)
    assert out["total"] == pytest.approx(172870.0)
    assert out["implied_non_span_rate_of_order_value"] == pytest.approx(0.02, abs=1e-4)


def test_icici_figures_flags_a_failed_response():
    assert runner._icici_figures(None)["ok"] is False
    assert runner._icici_figures({"Status": 500, "Error": "throttled"})["ok"] is False


def test_summary_ranks_combinations_and_reports_the_non_span_finding():
    results = [
        {
            "combinations": [
                {"span_method": "a", "elm_method": "x", "abs_diff_vs_icici_total": 10.0, "pct_vs_icici_total": 1.0},
                {"span_method": "b", "elm_method": "y", "abs_diff_vs_icici_total": 500.0, "pct_vs_icici_total": 50.0},
            ],
            "closest_combination": {"span_method": "a", "elm_method": "x", "abs_diff_vs_icici_total": 10.0},
            "icici": {"ok": True, "non_span_margin_required": 0.0},
        },
    ]
    summary = runner._summarise(results)
    assert summary["best_combination"]["span_method"] == "a"
    assert summary["best_combination"]["closest_on_cases"] == 1
    assert summary["icici_non_span_seen_non_zero"] is False
    assert summary["icici_non_span_sample_count"] == 1


def test_harness_refuses_to_run_outside_live_broker_mode(monkeypatch):
    """Mock mode fabricates margins; a comparison against fabricated numbers is worse than
    no comparison, because it looks like a result."""
    monkeypatch.setattr(cfg, "ICICI_BROKER_MODE", "mock")
    out = runner.run_harness("user-1")
    assert out["ok"] is False
    assert "live broker calls" in out["error"]


# --- persistence -------------------------------------------------------------------------


@pytest.fixture
def harness_db(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "DATA_PATH", str(tmp_path) + "/")
    monkeypatch.setattr(cfg, "USERS_DB", "users.sqlite3")
    store.ensure_margin_harness_tables()
    return tmp_path


def test_runs_persist_with_their_payload(harness_db):
    store.create_run("run-1", "user-1", "2026-09-07T10:00:00", 3)
    assert store.active_run_id() == "run-1"
    store.finish_run(
        "run-1",
        status="completed",
        finished_at="2026-09-07T10:02:00",
        priced_count=3,
        failed_count=0,
        broker_calls=3,
        summary={"best_combination": {"span_method": "a", "elm_method": "x"}},
        payload={"results": [1, 2, 3]},
    )
    assert store.active_run_id() is None
    runs = store.list_runs()
    assert len(runs) == 1
    assert runs[0]["status"] == "completed"
    assert runs[0]["summary"]["best_combination"]["span_method"] == "a"
    assert store.get_run_payload("run-1") == {"results": [1, 2, 3]}


def test_old_runs_are_pruned(harness_db):
    for i in range(store._MAX_RUNS_KEPT + 5):
        rid = f"run-{i:03d}"
        store.create_run(rid, "user-1", f"2026-09-07T10:{i:02d}:00", 1)
        store.finish_run(
            rid,
            status="completed",
            finished_at=f"2026-09-07T10:{i:02d}:30",
            priced_count=1,
            failed_count=0,
            broker_calls=1,
            summary={},
            payload={"n": i},
        )
    with sqlite3.connect(cfg.DATA_PATH + cfg.USERS_DB) as conn:
        kept = conn.execute("SELECT COUNT(*) FROM margin_harness_runs").fetchone()[0]
    assert kept == store._MAX_RUNS_KEPT
    # The newest survive, so a just-finished run is never the one pruned.
    assert store.get_run_payload(f"run-{store._MAX_RUNS_KEPT + 4:03d}") is not None


def test_run_route_reports_a_missing_icici_session_instead_of_starting():
    """A run that fails before its first case stores nothing, so the refusal has to come back
    from the route -- otherwise 'Run comparison' looks like it does nothing (2026-09-26)."""
    import asyncio
    from unittest.mock import patch

    from fastapi import HTTPException

    from icici_breeze_backend.app.api.v1 import route_settings as rs
    from icici_breeze_backend.app.auth.context import RequestContext
    from icici_breeze_backend.app.services.margin_harness import runner

    ctx = RequestContext(user_id="u1", username="u1", roles=["trader"], is_authenticated=True)
    with patch.object(rs.cfg, "ICICI_BROKER_MODE", "live"), patch.object(
        rs.breeze, "get_session_breeze", return_value=None
    ), patch.object(runner, "start_harness_run") as start:
        try:
            asyncio.run(rs.margin_harness_run(include_open_positions=True, ctx=ctx))
        except HTTPException as exc:
            assert exc.status_code == 503
            assert "No active ICICI session" in exc.detail
        else:
            raise AssertionError("expected a 503")
    start.assert_not_called()


def _addon_rates():
    from icici_breeze_backend.app.services.margin_addon import parse_rates

    return parse_rates(
        {
            "version": "t1",
            "index_rate": 0.02,
            "index_deep_otm_rate": 0.03,
            "index_deep_otm_threshold": 0.10,
            "stock_rate": 0.039,
            "stock_deep_otm_rate": 0.055,
            "stock_deep_otm_threshold": 0.30,
            "expiry_day_extra_rate": 0.02,
        }
    )


def _nifty_case(right="Call", action="Sell", strike=23150.0):
    from icici_breeze_backend.app.services.margin_harness.cases import CaseLeg, HarnessCase

    return HarnessCase(
        id=f"NIFTY:{right}:{action}",
        label="t",
        structure="t",
        source="generated",
        stock_code="NIFTY",
        exchange_code="NFO",
        expiry_date="29-Sep-2099",
        is_index=True,
        is_expiry_day=False,
        lot_size=65,
        spot_price=23140.5,
        spot_source="span_file:test",
        som_rate=0.0,
        legs=[
            CaseLeg(
                stock_code="NIFTY",
                exchange_code="NFO",
                expiry_date="29-Sep-2099",
                strike_price=strike,
                right=right,
                action=action,
                quantity=65,
            )
        ],
    )


def test_app_margin_is_span_file_plus_the_icici_addon():
    """Harness run 3250adaf: NIFTY 23150 CE short, SPAN 139,876.10 vs ICICI 170,501.85."""
    from icici_breeze_backend.app.services.margin_harness import runner

    span = {"som_floor_minus_nov_file": {"found": True, "span_margin": 139876.10}}
    out = runner._app_margin(_nifty_case(), span, 170501.85, _addon_rates())
    assert out["available"] and out["icici_addon"] == pytest.approx(30082.65, abs=0.01)
    assert out["total"] == pytest.approx(169958.75, abs=0.01)
    assert out["pct_vs_icici_total"] == pytest.approx(-0.318, abs=0.002)
    assert out["short_sides"] == "calls" and out["icici_addon_version"] == "t1"


def test_app_margin_says_why_when_the_addon_is_missing():
    from icici_breeze_backend.app.services.margin_harness import runner

    span = {"som_floor_minus_nov_file": {"found": True, "span_margin": 1.0}}
    out = runner._app_margin(_nifty_case(), span, 2.0, None)
    assert out["available"] is False and "portal" in out["reason"]


def test_app_method_summary_skips_long_only_and_splits_by_side():
    from icici_breeze_backend.app.services.margin_harness import runner

    def res(case, pct, sides):
        return {
            "case": case.as_dict(),
            "app_margin": {
                "available": True,
                "icici_addon_version": "t1",
                "pct_vs_icici_total": pct,
                "short_sides": sides,
            },
        }

    results = [
        res(_nifty_case("Call"), -1.0, "calls"),
        res(_nifty_case("Put", strike=22000.0), -3.0, "puts"),
        res(_nifty_case("Call", "Buy"), 0.0, "long_only"),
    ]
    s = runner._summarise_app_method(results)
    assert s["overall"]["cases"] == 2
    assert s["overall"]["mean_abs_pct"] == 2.0 and s["overall"]["mean_pct"] == -2.0
    assert s["by_group"]["short_puts"]["mean_abs_pct"] == 3.0
    assert s["by_group"]["nse_index"]["cases"] == 2 and s["by_group"]["stock"]["cases"] == 0
    assert s["addon_versions"] == ["t1"]


def test_marginism_is_offered_the_bse_pf_code_for_sensex(monkeypatch):
    """BSE's file names SENSEX options BSXOPT; the registry spellings alone never matched."""
    from icici_breeze_backend.app.services.margin_harness import marginism_engine as me

    case = _nifty_case()
    case = case.__class__(**{**case.__dict__, "stock_code": "BSESEN", "exchange_code": "BFO"})
    seen = {}

    def fake_load(path, name, candidates):
        seen["candidates"] = candidates
        raise LookupError("stop here")

    monkeypatch.setattr(me, "find_span_archive", lambda d, a: ("/x/BSERISK20260928-00.ZIP", True))
    monkeypatch.setattr(me, "aliases_for", lambda code, ex: ["BSESEN", "SENSEX"])
    monkeypatch.setattr(me, "_load", fake_load)
    monkeypatch.setattr(me, "_engines", {})
    me.evaluate(case, source_date="20260928", archive_name="BSERISK20260928-00.ZIP")
    assert "BSXOPT" in seen["candidates"]
