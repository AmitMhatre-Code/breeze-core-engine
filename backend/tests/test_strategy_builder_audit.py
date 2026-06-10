"""Tests for Strategy Builder (New) per-session audit logs."""
import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from icici_breeze_backend.app.services.options_strategy_engine import (
    EngineContext,
    QuoteRow,
    calc_bear_call_spread,
    run_propose_trades,
)
from icici_breeze_backend.audit.strategy_builder_audit import (
    StrategyBuilderAuditSession,
    audit_log_dir,
    quote_row_to_audit,
    resolve_audit_file_for_user,
)


class TestStrategyBuilderAuditSession(unittest.TestCase):
    def test_writes_json_audit_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch(
                "icici_breeze_backend.audit.strategy_builder_audit.audit_log_dir",
                return_value=tmp,
            ):
                session = StrategyBuilderAuditSession(
                    user_id="user-1",
                    request={"stock_code": "NIFTY", "range_lower": 22500, "range_upper": 24000},
                    request_id="req-abc",
                )
                session.record("test", "hello", {"x": 1}, rationale="because")
                path = session.finalize({"status": "ok"})
                self.assertTrue(os.path.isfile(path))
                with open(path, encoding="utf-8") as fh:
                    doc = json.load(fh)
                self.assertEqual(doc["user_id"], "user-1")
                self.assertEqual(doc["request_id"], "req-abc")
                self.assertEqual(doc["source"], "strategy_builder_new")
                self.assertEqual(len(doc["events"]), 1)
                self.assertEqual(doc["events"][0]["rationale"], "because")
                self.assertEqual(doc["summary"]["status"], "ok")
                self.assertEqual(doc["icici_api_calls"]["total"], 0)

    def test_api_call_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch(
                "icici_breeze_backend.audit.strategy_builder_audit.audit_log_dir",
                return_value=tmp,
            ):
                session = StrategyBuilderAuditSession(
                    user_id="u1",
                    request={"stock_code": "NIFTY"},
                )
                session.record_api_call("get_quote", {"strike": 23300}, {"Status": 200})
                session.record_api_call("get_quote", {"strike": 23350}, {"Status": 200})
                session.record_api_call(
                    "strategy_builder_margin", {"legs": []}, {"Status": 200}
                )
                session.set_temp_liquid_cache(
                    {
                        "liquid_ce_strikes": [23300],
                        "options": [],
                    }
                )
                path = session.finalize({"status": "ok"})
                with open(path, encoding="utf-8") as fh:
                    doc = json.load(fh)
                self.assertEqual(doc["icici_api_calls"]["total"], 3)
                self.assertEqual(doc["icici_api_calls"]["by_api"]["get_quote"], 2)
                self.assertEqual(doc["icici_api_calls"]["by_api"]["strategy_builder_margin"], 1)
                self.assertEqual(doc["temp_liquid_cache"]["liquid_ce_strikes"], [23300])

    def test_quote_row_to_audit_includes_liquid_flag(self):
        q = QuoteRow(
            strike=23300,
            right="Call",
            ltp=100.0,
            best_bid_price=99.0,
            best_offer_price=101.0,
            total_buy_qty=10,
            total_sell_qty=20,
            buy_sell_ratio=0.5,
        )
        out = quote_row_to_audit(q)
        self.assertTrue(out["liquid"])


class TestEngineAuditIntegration(unittest.TestCase):
    def _ctx(self, audit: StrategyBuilderAuditSession) -> EngineContext:
        strikes = list(range(23000, 24100, 50))
        cache = {}
        for s in strikes:
            for right in ("Call", "Put"):
                cache[(s, right)] = QuoteRow(
                    strike=s,
                    right=right,
                    ltp=50.0,
                    best_bid_price=49.0,
                    best_offer_price=51.0,
                    total_buy_qty=100,
                    total_sell_qty=100,
                    buy_sell_ratio=1.0,
                    spot_price=23500.0,
                )
        return EngineContext(
            processor=MagicMock(),
            user_id="u1",
            stock_code="NIFTY",
            exchange_code="NFO",
            expiry_display="09-Jun-2025",
            range_lower=23400,
            range_upper=23600,
            margin_rupees=500_000,
            max_loss_rupees=200_000,
            provision_elm=False,
            lot_size=75,
            strikes=strikes,
            strike_step=50,
            search_interval=50,
            spot=23500,
            atm_strike=23500,
            cache=cache,
            audit=audit,
        )

    def test_bear_call_audit_records_wing_search(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch(
                "icici_breeze_backend.audit.strategy_builder_audit.audit_log_dir",
                return_value=tmp,
            ):
                audit = StrategyBuilderAuditSession(
                    user_id="u1",
                    request={"stock_code": "NIFTY"},
                )
                res = calc_bear_call_spread(self._ctx(audit))
                audit.finalize({"status": "ok", "strategy": res.strategy_id})
                categories = [e["category"] for e in audit.events]
                self.assertIn("decision", categories)
                self.assertIn("strategy", categories)

    def test_run_propose_trades_emits_audit_metadata(self):
        strikes = list(range(23000, 24100, 50))
        proc = MagicMock()
        proc.fetch_lot_size.return_value = 75
        proc.list_option_strikes.return_value = strikes
        proc.strike_interval.return_value = 50
        proc.search_interval.return_value = 50
        proc.get_quote.return_value = {
            "Status": 200,
            "Success": [
                {
                    "ltp": 50.0,
                    "best_bid_price": 49.0,
                    "best_offer_price": 51.0,
                    "total_buy_qty": 100,
                    "total_sell_qty": 100,
                    "spot_price": 23500.0,
                }
            ],
        }
        proc.strategy_builder_margin.return_value = {
            "Status": 200,
            "Success": {"span_margin_required": 100_000},
        }

        with tempfile.TemporaryDirectory() as tmp:
            with patch(
                "icici_breeze_backend.audit.strategy_builder_audit.audit_log_dir",
                return_value=tmp,
            ):
                out = run_propose_trades(
                    proc,
                    "u1",
                    exchange_code="NFO",
                    stock_code="NIFTY",
                    expiry_date="09-Jun-2025",
                    range_lower=23400,
                    range_upper=23600,
                    margin_lacs=5.0,
                    max_loss_lacs=2.0,
                    provision_elm=False,
                    request_id="req-1",
                )
                self.assertEqual(out["Status"], 200)
                success = out["Success"]
                self.assertIn("audit_session_id", success)
                self.assertNotIn("audit_log_path", success)
                path = resolve_audit_file_for_user(
                    success["audit_session_id"], "u1"
                )
                self.assertIsNotNone(path)
                self.assertTrue(os.path.isfile(path))
                with open(path, encoding="utf-8") as fh:
                    doc = json.load(fh)
                self.assertGreater(doc["icici_api_calls"]["total"], 0)
                self.assertIn("temp_liquid_cache", doc)


if __name__ == "__main__":
    unittest.main()
