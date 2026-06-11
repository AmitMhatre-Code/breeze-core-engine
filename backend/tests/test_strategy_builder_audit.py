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
from icici_breeze_backend.app.services.processor import processor
from icici_breeze_backend.app.services.nsccl_baseline import MARGIN_SOURCE_EXCHANGE
from icici_breeze_backend.audit.strategy_builder_audit import (
    StrategyBuilderAuditSession,
    audit_log_dir,
    quote_row_to_audit,
    resolve_audit_file_for_user,
)

def _chain_row(strike: int, right: str) -> dict:
    return {
        "strike_price": strike,
        "ltp": 50.0,
        "best_bid_price": 49.0,
        "best_offer_price": 51.0,
        "total_buy_qty": 100,
        "total_sell_qty": 100,
        "spot_price": 23500.0,
        "right": right,
    }


def _mock_fetch_option_chain_quotes_sb(*_args, **kwargs):
    audit = kwargs.get("audit")
    strike_price = kwargs.get("strike_price")
    right = kwargs.get("right", "Call")
    if strike_price is None:
        strikes = list(range(23000, 24100, 50))
        res = {"Status": 200, "Success": [_chain_row(s, right) for s in strikes]}
        req = {"right": right, "full_chain": True}
    else:
        strike = int(strike_price)
        res = {"Status": 200, "Success": [_chain_row(strike, right)]}
        req = {"strike_price": strike_price, "right": right}
    if audit:
        audit.record_icici_api_call(
            "get_option_chain_quotes",
            req,
            res,
            rationale=kwargs.get("audit_rationale"),
        )
    return res


def _mock_strategy_builder_margin(*_args, **kwargs):
    res = {"Status": 200, "Success": {"span_margin_required": 100_000}}
    audit = kwargs.get("audit")
    if audit:
        audit.record_icici_api_call(
            "margin_calculator",
            {
                "exchange_code": _args[1] if len(_args) > 1 else "NFO",
                "margin_list": _args[2] if len(_args) > 2 else [],
                **(kwargs.get("audit_context") or {}),
            },
            res,
        )
    return res


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
                session.record_icici_api_call(
                    "get_option_chain_quotes", {"strike": 23300}, {"Status": 200}
                )
                session.record_icici_api_call(
                    "get_option_chain_quotes", {"strike": 23350}, {"Status": 503}
                )
                session.record_icici_api_call(
                    "margin_calculator", {"legs": []}, {"Status": 200}
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
                stats = doc["icici_api_calls"]
                self.assertEqual(stats["total"], 3)
                self.assertEqual(stats["total_success"], 2)
                self.assertEqual(stats["total_failed"], 1)
                self.assertEqual(stats["by_api"]["get_option_chain_quotes"]["total"], 2)
                self.assertEqual(stats["by_api"]["get_option_chain_quotes"]["success"], 1)
                self.assertEqual(stats["by_api"]["get_option_chain_quotes"]["failed"], 1)
                self.assertEqual(stats["by_api"]["margin_calculator"]["total"], 1)
                self.assertEqual(stats["by_api"]["margin_calculator"]["success"], 1)
                self.assertEqual(doc["temp_liquid_cache"]["liquid_ce_strikes"], [23300])
                api_events = [e for e in doc["events"] if e["category"] == "api_call"]
                self.assertTrue(all("success" in e["data"] for e in api_events))
                self.assertEqual(api_events[0]["data"]["api"], "get_option_chain_quotes")

    def test_icici_api_event_records_success_flag(self):
        session = StrategyBuilderAuditSession(user_id="u1", request={"stock_code": "NIFTY"})
        session.record_icici_api_call("margin_calculator", {}, {"Status": 400, "Error": "bad"})
        event = session.events[-1]
        self.assertFalse(event["data"]["success"])
        self.assertEqual(session.icici_api_call_stats["total_failed"], 1)

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
        proc.fetch_option_chain_quotes_sb.side_effect = _mock_fetch_option_chain_quotes_sb
        proc.strategy_builder_margin.side_effect = _mock_strategy_builder_margin

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
                chain_stats = doc["icici_api_calls"]["by_api"]["get_option_chain_quotes"]
                self.assertGreaterEqual(chain_stats["total"], 2)
                self.assertLess(chain_stats["total"], 20)
                self.assertIn("temp_liquid_cache", doc)


class TestProcessorIciciAudit(unittest.TestCase):
    def test_baseline_only_margin_skips_icici_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch(
                "icici_breeze_backend.audit.strategy_builder_audit.audit_log_dir",
                return_value=tmp,
            ):
                session = StrategyBuilderAuditSession(
                    user_id="u1",
                    request={"stock_code": "NIFTY"},
                )
                proc = processor()
                legs = [
                    {
                        "stock_code": "NIFTY",
                        "exchange_code": "NFO",
                        "expiry_date": "09-Jun-2025",
                        "product_type": "Options",
                        "right": "Call",
                        "strike_price": "23500",
                        "quantity": "75",
                        "action": "Sell",
                    }
                ]
                mock_breeze = MagicMock()
                with patch.object(proc, "get_session_breeze", return_value=mock_breeze), patch.object(
                    proc,
                    "get_strategy_builder_margin_source",
                    return_value=MARGIN_SOURCE_EXCHANGE,
                ), patch(
                    "icici_breeze_backend.app.services.processor.resolve_exchange_baseline_margin",
                    return_value={"found": True, "span_margin_required": 50_000.0},
                ):
                    res = proc.strategy_builder_margin(
                        "u1",
                        "NFO",
                        legs,
                        audit=session,
                        audit_context={"strategy_id": "naked_ce_short"},
                    )
                self.assertEqual(res["Status"], 200)
                self.assertEqual(session.icici_api_call_stats["total"], 0)
                mock_breeze.margin_calculator.assert_not_called()

    def test_margin_calculator_recorded_on_breeze_path(self):
        session = StrategyBuilderAuditSession(user_id="u1", request={"stock_code": "NIFTY"})
        proc = processor()
        legs = [
            {
                "stock_code": "NIFTY",
                "exchange_code": "NFO",
                "expiry_date": "09-Jun-2025",
                "product_type": "Options",
                "right": "Call",
                "strike_price": "23500",
                "quantity": "75",
                "action": "Sell",
            }
        ]
        mock_breeze = MagicMock()
        mock_breeze.margin_calculator.return_value = {
            "Status": 200,
            "Success": {"span_margin_required": 100_000},
        }
        with patch.object(proc, "get_session_breeze", return_value=mock_breeze), patch.object(
            proc,
            "get_strategy_builder_margin_source",
            return_value="breeze_api",
        ):
            proc.strategy_builder_margin("u1", "NFO", legs, audit=session)
        stats = session.icici_api_call_stats
        self.assertEqual(stats["total"], 1)
        self.assertEqual(stats["by_api"]["margin_calculator"]["success"], 1)
        mock_breeze.margin_calculator.assert_called_once()


if __name__ == "__main__":
    unittest.main()
