"""Unit tests for the tiered ELM (Extreme Loss Margin) formula and its wiring into
strategy_builder_margin."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from _pytest.monkeypatch import MonkeyPatch

from icici_breeze_backend.app.services.reference_data import symbol_registry as registry
from tests.fixtures.symbol_master import seed_symbol_master

from icici_breeze_backend.app.services.options_strategy_engine.helpers import (
    _otm_elm_rate,
    elm_addon,
    is_same_day_expiry,
)
from icici_breeze_backend.app.services.options_strategy_engine.types import TradeLeg


class TestOtmElmRate(unittest.TestCase):
    def test_index_standard_rate_at_the_money(self):
        self.assertEqual(_otm_elm_rate("Call", 23000, 23000, is_index=True), 0.02)

    def test_index_deep_otm_call_above_threshold(self):
        self.assertEqual(_otm_elm_rate("Call", 23000 * 1.15, 23000, is_index=True), 0.03)

    def test_index_standard_call_within_threshold(self):
        self.assertEqual(_otm_elm_rate("Call", 23000 * 1.05, 23000, is_index=True), 0.02)

    def test_index_deep_otm_put_below_threshold(self):
        self.assertEqual(_otm_elm_rate("Put", 23000 * 0.85, 23000, is_index=True), 0.03)

    def test_stock_standard_rate_at_the_money(self):
        self.assertEqual(_otm_elm_rate("Call", 2500, 2500, is_index=False), 0.05)

    def test_stock_deep_otm_call_above_threshold(self):
        self.assertEqual(_otm_elm_rate("Call", 2500 * 1.35, 2500, is_index=False), 0.0525)

    def test_stock_standard_call_within_threshold(self):
        self.assertEqual(_otm_elm_rate("Call", 2500 * 1.20, 2500, is_index=False), 0.05)


class TestElmAddon(unittest.TestCase):
    def test_flat_2pct_for_atm_index_short(self):
        legs = [TradeLeg("Call", "Sell", 23000, 75, 100.0)]
        elm = elm_addon(
            23000, 75, legs, provision_elm=True, is_index=True, previous_close=23000, same_day_expiry=False
        )
        self.assertAlmostEqual(elm, 23000 * 75 * 1 * 0.02)

    def test_same_day_expiry_zeroes_elm(self):
        legs = [TradeLeg("Call", "Sell", 23000, 75, 100.0)]
        elm = elm_addon(
            23000, 75, legs, provision_elm=True, is_index=True, previous_close=23000, same_day_expiry=True
        )
        self.assertEqual(elm, 0.0)

    def test_provision_elm_false_zeroes_elm(self):
        legs = [TradeLeg("Call", "Sell", 23000, 75, 100.0)]
        elm = elm_addon(
            23000, 75, legs, provision_elm=False, is_index=True, previous_close=23000, same_day_expiry=False
        )
        self.assertEqual(elm, 0.0)

    def test_buy_only_legs_contribute_zero(self):
        legs = [TradeLeg("Call", "Buy", 23000, 75, 100.0)]
        elm = elm_addon(
            23000, 75, legs, provision_elm=True, is_index=True, previous_close=23000, same_day_expiry=False
        )
        self.assertEqual(elm, 0.0)

    def test_mixed_basket_sums_standard_and_deep_otm_legs(self):
        spot = 23000
        legs = [
            TradeLeg("Call", "Sell", 23000, 75, 100.0),  # ATM -> standard 2%
            TradeLeg("Call", "Sell", 23000 * 1.15, 75, 20.0),  # 15% OTM -> deep 3%
        ]
        elm = elm_addon(
            spot, 75, legs, provision_elm=True, is_index=True, previous_close=spot, same_day_expiry=False
        )
        expected = spot * 75 * 1 * 0.02 + spot * 75 * 1 * 0.03
        self.assertAlmostEqual(elm, expected)

    def test_stock_deep_otm_put_uses_5_25_pct(self):
        spot = 2500
        legs = [TradeLeg("Put", "Sell", 2500 * 0.65, 500, 5.0)]  # 35% OTM put
        elm = elm_addon(
            spot, 500, legs, provision_elm=True, is_index=False, previous_close=spot, same_day_expiry=False
        )
        expected = spot * 500 * 1 * 0.0525
        self.assertAlmostEqual(elm, expected)

    def test_previous_close_none_falls_back_to_spot(self):
        legs = [TradeLeg("Call", "Sell", 23000, 75, 100.0)]
        elm = elm_addon(
            23000, 75, legs, provision_elm=True, is_index=True, previous_close=None, same_day_expiry=False
        )
        self.assertAlmostEqual(elm, 23000 * 75 * 1 * 0.02)


class TestIsSameDayExpiry(unittest.TestCase):
    def test_today_iso_format(self):
        from icici_breeze_backend.app.core.timezone import today_ist_date

        self.assertTrue(is_same_day_expiry(today_ist_date().strftime("%Y-%m-%d")))

    def test_today_display_format(self):
        from icici_breeze_backend.app.core.timezone import today_ist_date

        self.assertTrue(is_same_day_expiry(today_ist_date().strftime("%d-%b-%Y")))

    def test_future_date_is_not_same_day(self):
        self.assertFalse(is_same_day_expiry("31-Dec-2099"))

    def test_invalid_date_is_not_same_day(self):
        self.assertFalse(is_same_day_expiry("not-a-date"))


class TestIndexClassification(unittest.TestCase):
    """ELM tiering keys off the Security-Master-backed registry rather than a hand-kept name
    set, because broker-facing paths carry the ICICI ShortName (BSESEN, CNXBAN) and a set
    spelled in exchange names silently charged the 5% single-stock tier on index shorts."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._mp = MonkeyPatch()
        seed_symbol_master(Path(self._tmp.name), self._mp)

    def tearDown(self):
        self._mp.undo()
        self._tmp.cleanup()
        registry.clear_cache()

    def test_icici_short_names_classify_as_index(self):
        for short_name in ("NIFTY", "CNXBAN", "NIFSEL", "BSESEN", "BANKEX"):
            with self.subTest(short_name=short_name):
                self.assertIs(registry.is_index(short_name), True)

    def test_exchange_style_names_classify_as_index(self):
        for name in ("NIFTY 50", "NIFTY BANK", "SENSEX", "MIDCPNIFTY"):
            with self.subTest(name=name):
                self.assertIs(registry.is_index(name), True)

    def test_single_stock_is_not_index(self):
        for name in ("INFTEC", "RELIND", "INFY"):
            with self.subTest(name=name):
                self.assertIs(registry.is_index(name), False)

    def test_unknown_underlying_is_unknown(self):
        self.assertIsNone(registry.is_index("NOSUCH"))


def _legs(expiry="09-Jun-2099", stock_code="NIFTY", exchange_code="NFO", strike="23500", quantity="75"):
    return [
        {
            "stock_code": stock_code,
            "exchange_code": exchange_code,
            "expiry_date": expiry,
            "product_type": "Options",
            "right": "Call",
            "strike_price": strike,
            "quantity": quantity,
            "action": "Sell",
        }
    ]


class TestStrategyBuilderMarginElm(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._mp = MonkeyPatch()
        seed_symbol_master(Path(self._tmp.name), self._mp)

    def tearDown(self):
        self._mp.undo()
        self._tmp.cleanup()
        registry.clear_cache()

    def _mock_breeze(self):
        mock_breeze = MagicMock()
        mock_breeze.margin_calculator.return_value = {
            "Status": 200,
            "Success": {"span_margin_required": 100_000},
        }
        return mock_breeze

    def test_elm_attached_when_spot_provided_and_bhav_lookup_succeeds(self):
        from icici_breeze_backend.app.services.processor import processor

        proc = processor()
        with patch.object(proc, "get_session_breeze", return_value=self._mock_breeze()), patch.object(
            proc, "get_strategy_builder_margin_source", return_value="breeze_api"
        ), patch.object(proc, "fetch_lot_size", return_value=75), patch(
            "icici_breeze_backend.app.services.processor._lookup_bhav_row",
            return_value={"spot_price": "23300"},
        ):
            res = proc.strategy_builder_margin("u1", "NFO", _legs(), spot=23310)
        self.assertEqual(res["Status"], 200)
        success = res["Success"]
        self.assertTrue(success["elm_is_index"])
        self.assertFalse(success["elm_approximate"])
        expected = round(23310 * 75 * 1 * 0.02, 2)
        self.assertAlmostEqual(success["elm_requirement"], expected)

    def test_elm_falls_back_to_spot_and_flags_approximate_when_bhav_lookup_misses(self):
        from icici_breeze_backend.app.services.processor import processor

        proc = processor()
        with patch.object(proc, "get_session_breeze", return_value=self._mock_breeze()), patch.object(
            proc, "get_strategy_builder_margin_source", return_value="breeze_api"
        ), patch.object(proc, "fetch_lot_size", return_value=75), patch(
            "icici_breeze_backend.app.services.processor._lookup_bhav_row", return_value=None
        ):
            res = proc.strategy_builder_margin("u1", "NFO", _legs(), spot=23310)
        success = res["Success"]
        self.assertIsNotNone(success.get("elm_requirement"))
        self.assertTrue(success["elm_approximate"])

    def test_elm_zero_on_same_day_expiry(self):
        from icici_breeze_backend.app.services.processor import processor
        from icici_breeze_backend.app.core.timezone import today_ist_date

        proc = processor()
        today_display = today_ist_date().strftime("%d-%b-%Y")
        with patch.object(proc, "get_session_breeze", return_value=self._mock_breeze()), patch.object(
            proc, "get_strategy_builder_margin_source", return_value="breeze_api"
        ), patch.object(proc, "fetch_lot_size", return_value=75):
            res = proc.strategy_builder_margin("u1", "NFO", _legs(expiry=today_display), spot=23310)
        success = res["Success"]
        self.assertEqual(success.get("elm_requirement"), 0.0)

    def test_sensex_short_name_uses_the_2pct_index_tier(self):
        """BSESEN (ICICI's ShortName for SENSEX) is an index: a 5%-OTM short must be charged 2%,
        not the 5% single-stock tier."""
        from icici_breeze_backend.app.services.processor import processor

        proc = processor()
        with patch.object(proc, "get_session_breeze", return_value=self._mock_breeze()), patch.object(
            proc, "get_strategy_builder_margin_source", return_value="breeze_api"
        ), patch.object(proc, "fetch_lot_size", return_value=20), patch(
            "icici_breeze_backend.app.services.processor._lookup_bhav_row",
            return_value={"spot_price": "75119.80"},
        ):
            res = proc.strategy_builder_margin(
                "u1",
                "BFO",
                _legs(stock_code="BSESEN", exchange_code="BFO", strike="78900", quantity="3640"),
                spot=75119.80,
            )
        success = res["Success"]
        self.assertTrue(success["elm_is_index"])
        self.assertAlmostEqual(success["elm_requirement"], round(75119.80 * 3640 * 0.02, 2))

    def test_unknown_underlying_falls_back_to_stock_tier_and_flags_approximate(self):
        """Not knowing must not read as "not an index": the safe direction is the higher
        single-stock tier, and the response says the number is approximate."""
        from icici_breeze_backend.app.services.processor import processor

        proc = processor()
        with patch.object(proc, "get_session_breeze", return_value=self._mock_breeze()), patch.object(
            proc, "get_strategy_builder_margin_source", return_value="breeze_api"
        ), patch.object(proc, "fetch_lot_size", return_value=75), patch(
            "icici_breeze_backend.app.services.processor._lookup_bhav_row",
            return_value={"spot_price": "23300"},
        ):
            res = proc.strategy_builder_margin(
                "u1", "NFO", _legs(stock_code="NOSUCH"), spot=23310
            )
        success = res["Success"]
        self.assertFalse(success["elm_is_index"])
        self.assertTrue(success["elm_approximate"])
        self.assertAlmostEqual(success["elm_requirement"], round(23310 * 75 * 1 * 0.05, 2))

    def test_no_elm_field_when_spot_not_provided(self):
        from icici_breeze_backend.app.services.processor import processor

        proc = processor()
        with patch.object(proc, "get_session_breeze", return_value=self._mock_breeze()), patch.object(
            proc, "get_strategy_builder_margin_source", return_value="breeze_api"
        ):
            res = proc.strategy_builder_margin("u1", "NFO", _legs())
        success = res["Success"]
        self.assertNotIn("elm_requirement", success)


if __name__ == "__main__":
    unittest.main()
