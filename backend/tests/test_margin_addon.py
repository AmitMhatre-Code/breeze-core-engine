"""ICICI margin add-on from the portal, and the three "use the SPAN file" toggles (#48)."""
from __future__ import annotations

import datetime as dt
import sqlite3
import time
from unittest.mock import MagicMock, patch

import pytest

from icici_breeze_backend.app.services import margin_addon, margin_source_prefs
from icici_breeze_backend.app.services.margin_addon import (
    MarginAddonRates,
    compute_addon,
    parse_rates,
)
from icici_breeze_backend.app.services.nsccl_baseline import (
    MARGIN_SOURCE_BREEZE,
    MARGIN_SOURCE_EXCHANGE,
)
from icici_breeze_backend.app.services.portal_policy_token import verify_policy_token
from icici_breeze_backend.app.services.reference_data import span_freshness, span_sources
from tests.fixtures.margin_addon import active_margin_addon
from tests.fixtures.portal_heartbeat_drm_keys import attach_test_policy_token

CLAIM = {
    "version": "v7",
    "index_rate": 0.02,
    "index_deep_otm_rate": 0.03,
    "index_deep_otm_threshold": 0.10,
    "stock_rate": 0.039,
    "stock_deep_otm_rate": 0.055,
    "stock_deep_otm_threshold": 0.30,
    "expiry_day_extra_rate": 0.02,
}
RATES = parse_rates(CLAIM)


@pytest.fixture
def users_db(tmp_path, monkeypatch):
    path = tmp_path / "users.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.execute(
            """CREATE TABLE user_account (
                user_id TEXT PRIMARY KEY,
                strategy_builder_margin_source TEXT NOT NULL DEFAULT 'breeze_api'
            )"""
        )
        conn.execute("INSERT INTO user_account (user_id) VALUES ('existing')")
        conn.commit()
    monkeypatch.setattr(margin_addon.cfg, "DATA_PATH", str(tmp_path) + "/")
    monkeypatch.setattr(margin_addon.cfg, "USERS_DB", "users.sqlite3")
    return path


def _leg(strike, right="Call", action="Sell", qty=65, expiry="29-Sep-2099"):
    return {"strike_price": strike, "right": right, "action": action, "quantity": qty, "expiry_date": expiry}


# --- rates ---------------------------------------------------------------------------


def test_parse_rates_accepts_the_portal_claim():
    assert RATES is not None
    assert RATES.version == "v7" and RATES.stock_rate == 0.039


@pytest.mark.parametrize(
    "bad",
    [
        None,
        "0.02",
        {**CLAIM, "version": ""},
        {**CLAIM, "index_rate": -0.01},
        {**CLAIM, "index_rate": 0.5},
        {**CLAIM, "stock_rate": True},
        {k: v for k, v in CLAIM.items() if k != "expiry_day_extra_rate"},
    ],
)
def test_parse_rates_rejects_unusable_claims(bad):
    assert parse_rates(bad) is None


# --- arithmetic (numbers from harness run fac6f1e5) ---------------------------------------


def test_short_atm_index_call_pays_the_index_rate_on_spot_notional():
    amount = compute_addon(RATES, [_leg(23150)], spot=23140.5, is_index=True)
    assert amount == pytest.approx(0.02 * 23140.5 * 65, abs=0.01)


def test_deep_otm_index_call_pays_the_deep_tier():
    amount = compute_addon(RATES, [_leg(26600)], spot=23140.5, is_index=True)  # 14.9% OTM
    assert amount == pytest.approx(0.03 * 23140.5 * 65, abs=0.01)


def test_stock_tiers_switch_at_thirty_percent():
    near = compute_addon(RATES, [_leg(1540, qty=500)], spot=1224.6, is_index=False)  # 25.8% OTM
    far = compute_addon(RATES, [_leg(2800, qty=225)], spot=2081.9, is_index=False)  # 34.5% OTM
    assert near == pytest.approx(0.039 * 1224.6 * 500, abs=0.01)
    assert far == pytest.approx(0.055 * 2081.9 * 225, abs=0.01)


def test_longs_pay_nothing_and_wings_do_not_reduce_short_legs():
    condor = [
        _leg(24300, "Call", "Sell"),
        _leg(25450, "Call", "Buy"),
        _leg(22000, "Put", "Sell"),
        _leg(20850, "Put", "Buy"),
    ]
    assert compute_addon(RATES, condor, spot=23140.5, is_index=True) == pytest.approx(
        2 * 0.02 * 23140.5 * 65, abs=0.01
    )
    assert compute_addon(RATES, [_leg(23150, action="Buy")], spot=23140.5, is_index=True) == 0.0


def test_a_buy_of_the_same_contract_nets_the_short_first():
    legs = [_leg(23150, qty=130), _leg(23150, action="Buy", qty=65)]
    assert compute_addon(RATES, legs, spot=23140.5, is_index=True) == pytest.approx(
        0.02 * 23140.5 * 65, abs=0.01
    )


def test_expiry_day_adds_the_extra_rate_only_to_legs_expiring_today():
    today = dt.date(2099, 9, 29)
    legs = [_leg(23150, expiry="29-Sep-2099"), _leg(23150, expiry="06-Oct-2099")]
    amount = compute_addon(RATES, legs, spot=23140.5, is_index=True, today=today)
    assert amount == pytest.approx((0.04 + 0.02) * 23140.5 * 65, abs=0.01)


# --- persistence and the 24h fail-closed rule ------------------------------------------------


def test_nothing_received_means_no_addon_and_icici_everywhere(users_db):
    assert margin_addon.get_active_rates() is None
    st = margin_addon.status()
    assert st["available"] is False and st["reason"] == "never_received"
    assert "heartbeat" in st["message"]
    assert margin_source_prefs.effective_margin_source("existing", "app") == MARGIN_SOURCE_BREEZE


def test_received_rates_are_active_for_24_hours_then_lapse(users_db):
    assert margin_addon.record_from_policy({"margin_addon": CLAIM}) is True
    now = time.time()
    assert margin_addon.get_active_rates(now_epoch=now) == RATES
    assert margin_addon.get_active_rates(now_epoch=now + 23 * 3600) == RATES
    assert margin_addon.get_active_rates(now_epoch=now + 25 * 3600) is None
    st = margin_addon.status(now_epoch=now + 25 * 3600)
    assert st["available"] is False and st["reason"] == "stale"
    assert st["rates"]["version"] == "v7"


def test_a_policy_without_or_with_a_bad_claim_keeps_the_last_good_rates(users_db):
    margin_addon.record_from_policy({"margin_addon": CLAIM})
    assert margin_addon.record_from_policy({"status": "OK"}) is False
    assert margin_addon.record_from_policy({"margin_addon": {**CLAIM, "index_rate": "x"}}) is False
    assert margin_addon.get_active_rates() == RATES


# --- toggles ---------------------------------------------------------------------------------


def test_every_scope_defaults_to_the_span_file_including_existing_users(users_db):
    margin_source_prefs.ensure_margin_source_columns(str(users_db))
    for scope in margin_source_prefs.SCOPES:
        assert margin_source_prefs.get_user_choice("existing", scope) == MARGIN_SOURCE_EXCHANGE


def test_toggles_fall_back_to_icici_and_return_to_the_users_choice(users_db):
    margin_source_prefs.set_user_choice("existing", "backtest", MARGIN_SOURCE_EXCHANGE)
    margin_source_prefs.set_user_choice("existing", "app", MARGIN_SOURCE_BREEZE)
    # No add-on: every scope prices from ICICI, but the stored choice is untouched.
    assert margin_source_prefs.effective_margin_source("existing", "backtest") == MARGIN_SOURCE_BREEZE
    assert margin_source_prefs.get_user_choice("existing", "backtest") == MARGIN_SOURCE_EXCHANGE
    # Add-on arrives: the scope returns to what the user picked.
    margin_addon.record_from_policy({"margin_addon": CLAIM})
    assert margin_source_prefs.effective_margin_source("existing", "backtest") == MARGIN_SOURCE_EXCHANGE
    assert margin_source_prefs.effective_margin_source("existing", "app") == MARGIN_SOURCE_BREEZE


def test_unknown_scope_is_refused(users_db):
    with pytest.raises(ValueError):
        margin_source_prefs.set_user_choice("existing", "dashboard", MARGIN_SOURCE_EXCHANGE)


# --- signed policy claim -----------------------------------------------------------------------


def test_policy_token_carries_the_margin_addon_claim():
    body = attach_test_policy_token(
        {"status": "OK", "deployment_license_status": "active", "margin_addon": CLAIM},
        public_ip="203.0.113.10",
    )
    policy = verify_policy_token(body["policy_token"], public_ip="203.0.113.10")
    assert policy["margin_addon"] == CLAIM


def test_a_malformed_margin_addon_drops_only_that_claim():
    body = attach_test_policy_token(
        {
            "status": "OK",
            "deployment_license_status": "active",
            "margin_addon": {**CLAIM, "index_rate": 9},
        },
        public_ip="203.0.113.10",
    )
    policy = verify_policy_token(body["policy_token"], public_ip="203.0.113.10")
    assert "margin_addon" not in policy
    assert policy["deployment_license_status"] == "active"


# --- SPAN file margins carry the add-on ------------------------------------------------------------


def _sb_legs():
    return [
        {
            "stock_code": "NIFTY",
            "exchange_code": "NFO",
            "expiry_date": "29-Sep-2099",
            "product_type": "Options",
            "right": "Call",
            "strike_price": "23150",
            "quantity": "65",
            "action": "Sell",
        }
    ]


def test_strategy_builder_span_margin_includes_the_addon():
    from icici_breeze_backend.app.services.processor import processor

    proc = processor()
    mock_breeze = MagicMock()
    with active_margin_addon(RATES, spot=23140.5), patch.object(
        proc, "get_session_breeze", return_value=mock_breeze
    ), patch(
        "icici_breeze_backend.app.services.processor.resolve_exchange_baseline_margin",
        return_value={"found": True, "span_margin_required": 136121.05},
    ), patch.object(
        proc,
        "_portfolio_baseline_span_margin",
        return_value={"found": True, "span_margin_required": 136121.05},
    ):
        res = proc.strategy_builder_margin(
            "u1", "NFO", _sb_legs(), margin_source_override=MARGIN_SOURCE_EXCHANGE
        )
    success = res["Success"]
    addon = round(0.02 * 23140.5 * 65, 2)
    assert success["margin_source"] == MARGIN_SOURCE_EXCHANGE
    assert success["span_file_margin"] == 136121.05
    assert success["icici_addon"] == pytest.approx(addon)
    assert success["span_margin_required"] == pytest.approx(136121.05 + addon, abs=0.01)
    assert success["icici_addon_version"] == "v7"
    mock_breeze.margin_calculator.assert_not_called()


def test_strategy_builder_asks_icici_when_the_addon_is_missing():
    from icici_breeze_backend.app.services.processor import processor

    proc = processor()
    mock_breeze = MagicMock()
    mock_breeze.margin_calculator.return_value = {"Status": 200, "Success": {"span_margin_required": 170501.85}}
    with patch.object(margin_addon, "get_active_rates", return_value=None), patch.object(
        margin_source_prefs, "get_active_rates", return_value=None
    ), patch.object(proc, "get_session_breeze", return_value=mock_breeze), patch.object(
        proc, "_maybe_evict_session"
    ):
        res = proc.strategy_builder_margin(
            "u1", "NFO", _sb_legs(), margin_source_override=MARGIN_SOURCE_EXCHANGE
        )
    assert res["Success"]["margin_source"] == MARGIN_SOURCE_BREEZE
    assert res["Success"]["span_margin_required"] == 170501.85
    mock_breeze.margin_calculator.assert_called_once()


def test_portfolio_groups_use_the_span_file_when_the_app_toggle_is_on():
    from icici_breeze_backend.app.services.processor import processor

    proc = processor()
    legs = [
        {**_sb_legs()[0], "carry_profit": 100.0, "elm_margin_required": None},
    ]
    with active_margin_addon(RATES, spot=23140.5), patch.object(
        proc, "get_margin_source", return_value=MARGIN_SOURCE_EXCHANGE
    ), patch.object(
        proc,
        "_portfolio_baseline_span_margin",
        return_value={"found": True, "span_margin_required": 136121.05},
    ), patch.object(proc, "_netted_span_for_legs") as icici:
        out = proc._compute_netted_margins(MagicMock(), "u1", legs)
    addon = 0.02 * 23140.5 * 65
    assert out["groups"][0]["margin_source"] == MARGIN_SOURCE_EXCHANGE
    assert out["groups"][0]["span_margin_required"] == pytest.approx(136121.05 + addon, abs=0.01)
    assert out["portfolio"]["margin_source"] == MARGIN_SOURCE_EXCHANGE
    icici.assert_not_called()


# --- SPAN file freshness and next-day files ---------------------------------------------------------


def test_next_trading_day_skips_the_weekend():
    with patch(
        "icici_breeze_backend.app.services.market_calendar.is_trading_day",
        side_effect=lambda d: d.weekday() < 5,
    ):
        assert span_sources.next_trading_day(dt.date(2026, 9, 25)) == dt.date(2026, 9, 28)
        assert span_sources.next_trading_day(dt.date(2026, 9, 26)) == dt.date(2026, 9, 28)


def test_nse_resolver_prefers_the_next_trading_days_i1(monkeypatch):
    probed: list[str] = []

    def exists(url, market):
        probed.append(url.rsplit("/", 1)[-1])
        return url.endswith("nsccl.20260928.i1.zip")

    monkeypatch.setattr(span_sources, "_url_exists", exists)
    monkeypatch.setattr(span_sources, "today_ist_date", lambda: dt.date(2026, 9, 26))
    monkeypatch.setattr(span_sources, "next_trading_day", lambda d: dt.date(2026, 9, 28))
    ref = span_sources.resolve_latest_nse_span_archive()
    assert ref.archive_name == "nsccl.20260928.i1.zip" and ref.source_date == "20260928"
    assert probed == ["nsccl.20260928.i1.zip"]


def test_bse_resolver_prefers_the_next_trading_days_beginning_of_day(monkeypatch):
    monkeypatch.setattr(span_sources, "_url_exists", lambda url, market: url.endswith("BSERISK20260928-00.ZIP"))
    monkeypatch.setattr(span_sources, "today_ist_date", lambda: dt.date(2026, 9, 26))
    monkeypatch.setattr(span_sources, "next_trading_day", lambda d: dt.date(2026, 9, 28))
    ref = span_sources.resolve_latest_bse_span_archive()
    assert ref.archive_name == "BSERISK20260928-00.ZIP" and ref.source_version == 0


def test_resolver_walks_back_when_the_next_day_file_is_not_out_yet(monkeypatch):
    monkeypatch.setattr(span_sources, "_url_exists", lambda url, market: url.endswith("nsccl.20260925.i5.zip"))
    monkeypatch.setattr(span_sources, "today_ist_date", lambda: dt.date(2026, 9, 25))
    monkeypatch.setattr(span_sources, "next_trading_day", lambda d: dt.date(2026, 9, 28))
    assert span_sources.resolve_latest_nse_span_archive().archive_name == "nsccl.20260925.i5.zip"


@pytest.mark.parametrize(
    "today, loaded, outdated",
    [
        (dt.date(2026, 9, 26), "20260925", False),  # Saturday on Friday's file
        (dt.date(2026, 9, 26), "20260928", False),  # Saturday on Monday's BOD file
        (dt.date(2026, 9, 28), "20260925", True),  # Monday on Friday's file
        (dt.date(2026, 9, 26), "20260923", True),  # the stale BSE file of run fac6f1e5
        (dt.date(2026, 9, 26), None, True),
    ],
)
def test_span_file_freshness(monkeypatch, today, loaded, outdated):
    monkeypatch.setattr(span_freshness, "_loaded_source_date", lambda code: loaded)
    with patch(
        "icici_breeze_backend.app.services.market_calendar.is_trading_day",
        side_effect=lambda d: d.weekday() < 5,
    ):
        fresh = span_freshness.span_file_freshness(today)
        assert fresh["exchanges"]["NFO"]["outdated"] is outdated
        message = span_freshness.outdated_message("NFO", today)
    assert (message is not None) is outdated


# --- backtest sizing ----------------------------------------------------------------------------


def _fly_config():
    cfg_ = MagicMock()
    cfg_.margin_ceiling_inr = 1_000_000
    cfg_.min_lots = 1
    return cfg_


def test_backtest_sizes_from_the_span_file_and_warns_when_it_is_outdated(monkeypatch):
    from icici_breeze_backend.app.services.bots import backtest_service as svc

    proc = MagicMock()
    proc._span_file_margin_for_legs.return_value = 100_000.0
    monkeypatch.setattr(svc, "_spot_and_contract", lambda *a: (23140.5, "29-Sep-2099", 65))
    monkeypatch.setattr(svc, "_latest_vix", lambda path: 12.0)
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.bots.scalping.iron_fly_bot.wing_width_for", lambda c, v: 300
    )
    monkeypatch.setattr(span_freshness, "outdated_message", lambda code: "NSE file is outdated")
    with patch("icici_breeze_backend.app.services.bots.scalping.margin.margin_for_mixed_legs") as icici:
        out = svc.price_lots("fly", _fly_config(), "u1", proc, margin_source=MARGIN_SOURCE_EXCHANGE)
    assert out["lots"] == 10 and out["margin_source"] == "span_file"
    assert out["notes"] == ["NSE file is outdated"]
    icici.assert_not_called()
    rows = proc._span_file_margin_for_legs.call_args.args[1]
    assert {(r["right"], r["action"]) for r in rows} == {("Call", "Buy"), ("Put", "Buy"), ("Call", "Sell"), ("Put", "Sell")}


def test_backtest_falls_back_to_icici_per_structure_the_file_cannot_price(monkeypatch):
    from icici_breeze_backend.app.services.bots import backtest_service as svc

    proc = MagicMock()
    proc._span_file_margin_for_legs.return_value = None
    monkeypatch.setattr(svc, "_spot_and_contract", lambda *a: (23140.5, "29-Sep-2099", 65))
    monkeypatch.setattr(svc, "_latest_vix", lambda path: 12.0)
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.bots.scalping.iron_fly_bot.wing_width_for", lambda c, v: 300
    )
    monkeypatch.setattr(span_freshness, "outdated_message", lambda code: None)
    with patch(
        "icici_breeze_backend.app.services.bots.scalping.margin.margin_for_mixed_legs",
        return_value=200_000.0,
    ) as icici:
        out = svc.price_lots("fly", _fly_config(), "u1", proc, margin_source=MARGIN_SOURCE_EXCHANGE)
    assert out["lots"] == 5
    icici.assert_called_once()
    assert "ICICI's margin calculator was used" in out["notes"][0]


def test_backtest_on_icici_never_touches_the_span_file(monkeypatch):
    from icici_breeze_backend.app.services.bots import backtest_service as svc

    proc = MagicMock()
    monkeypatch.setattr(svc, "_spot_and_contract", lambda *a: (23140.5, "29-Sep-2099", 65))
    monkeypatch.setattr(svc, "_latest_vix", lambda path: 12.0)
    monkeypatch.setattr(
        "icici_breeze_backend.app.services.bots.scalping.iron_fly_bot.wing_width_for", lambda c, v: 300
    )
    with patch(
        "icici_breeze_backend.app.services.bots.scalping.margin.margin_for_mixed_legs",
        return_value=200_000.0,
    ):
        out = svc.price_lots("fly", _fly_config(), "u1", proc, margin_source=MARGIN_SOURCE_BREEZE)
    assert out["margin_source"] == "icici" and out["notes"] == []
    proc._span_file_margin_for_legs.assert_not_called()
