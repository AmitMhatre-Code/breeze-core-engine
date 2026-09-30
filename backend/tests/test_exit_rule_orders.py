"""B-51: an order that merely matches a GTT's contract and side is never hidden."""
from __future__ import annotations

from icici_breeze_backend.app.domain.gtt_exit_order import GttExitOrderLeg, GttExitOrderRowRecord
from icici_breeze_backend.app.services.bots.scalping import order_intents
from icici_breeze_backend.app.services.exit_rule_orders import split_rule_spawned_orders


def _gtt():
    return GttExitOrderRowRecord(
        gtt_order_id="G1", stock_code="NIFTY", exchange_code="NFO", expiry_display="06-Oct-2026",
        strike_price="25000", right="call", legs=[GttExitOrderLeg(action="Sell")],
    )


def _order(order_id, remark=""):
    return {
        "order_id": order_id, "stock_code": "NIFTY", "exchange_code": "NFO",
        "expiry_date": "06-Oct-2026", "strike_price": "25000", "right": "Call",
        "action": "Sell", "user_remark": remark,
    }


def test_the_apps_own_order_is_never_taken_for_the_gtts():
    kept, excluded = split_rule_spawned_orders([], [_gtt()], [_order("A1", order_intents.new_tag())])
    assert excluded == []
    assert "possible_gtt_exit_id" not in kept[0]


def test_an_untagged_match_stays_in_the_order_book_marked():
    kept, excluded = split_rule_spawned_orders([], [_gtt()], [_order("M1")])
    assert excluded == []
    assert kept[0]["order_id"] == "M1" and kept[0]["possible_gtt_exit_id"] == "G1"


def test_app_tags_have_a_recognisable_shape():
    tag = order_intents.new_tag()
    assert len(tag) == 8 and tag.startswith("bm") and tag.isalpha() and tag.islower()
    assert order_intents.is_app_tag(tag)
    assert not order_intents.is_app_tag("")
    assert not order_intents.is_app_tag("fmtcheck")
    assert not order_intents.is_app_tag("BMABCDEF")
