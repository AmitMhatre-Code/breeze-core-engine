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
