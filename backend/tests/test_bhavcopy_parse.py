"""Tests for bhavcopy row normalization."""
from icici_breeze_backend.app.services.reference_data.bhavcopy_common import (
    normalize_bse_fo_bhavcopy_row,
    normalize_nse_fo_bhavcopy_row,
)


def test_normalize_nse_fo_bhavcopy_row():
    raw = {
        "TckrSymb": "NIFTY",
        "XpryDt": "2026-06-30",
        "OptnTp": "CE",
        "StrkPric": "25000",
        "ClsPric": "120.5",
        "BidPric": "120",
        "AskPric": "121",
        "TtlTradgVol": "1000",
        "OpnIntrst": "50000",
        "UndrlygPric": "24800",
    }
    row = normalize_nse_fo_bhavcopy_row(raw)
    assert row is not None
    assert row["stock_code"] == "NIFTY"
    assert row["expiry_display"] == "30-Jun-2026"
    assert row["right"] == "Call"
    assert row["strike_price"] == "25000"
    # Quoted prices pass through when the file carried them...
    assert row["best_bid_price"] == "120.00"
    assert row["best_offer_price"] == "121.00"
    # ...but depth is never synthesized from traded volume.
    assert "total_buy_qty" not in row
    assert "total_sell_qty" not in row


def test_normalize_nse_fo_bhavcopy_row_omits_absent_quotes():
    """No bid/ask columns must stay absent rather than defaulting to the close."""
    row = normalize_nse_fo_bhavcopy_row(
        {
            "TckrSymb": "NIFTY",
            "XpryDt": "2026-06-30",
            "OptnTp": "CE",
            "StrkPric": "25000",
            "ClsPric": "120.5",
            "TtlTradgVol": "1000",
            "OpnIntrst": "50000",
        }
    )
    assert row is not None
    assert row["ltp"] == "120.50"
    assert "best_bid_price" not in row
    assert "best_offer_price" not in row


def test_normalize_nse_fo_prefers_last_traded_price_for_ltp():
    """ltp must be LastPric (real traded price), not ClsPric (settlement)."""
    row = normalize_nse_fo_bhavcopy_row(
        {
            "TckrSymb": "NIFTY",
            "XpryDt": "2026-06-30",
            "OptnTp": "CE",
            "StrkPric": "25000",
            "ClsPric": "120.5",
            "LastPric": "118.75",
            "OpnIntrst": "50000",
        }
    )
    assert row is not None
    assert row["ltp"] == "118.75"


def test_normalize_nse_fo_falls_back_to_close_when_no_last_traded():
    """Illiquid contract that didn't trade (LastPric 0/absent) keeps the settlement close."""
    row = normalize_nse_fo_bhavcopy_row(
        {
            "TckrSymb": "NIFTY",
            "XpryDt": "2026-06-30",
            "OptnTp": "CE",
            "StrkPric": "25000",
            "ClsPric": "120.5",
            "LastPric": "0",
            "OpnIntrst": "50000",
        }
    )
    assert row is not None
    assert row["ltp"] == "120.50"


def test_normalize_bse_fo_bhavcopy_row():
    raw = {
        "TCKR_SYMB": "SENSEX",
        "XPRY_DT": "2026-06-26",
        "OPTN_TP": "PE",
        "STRK_PRIC": "82000",
        "CLSPRIC": "95.25",
        "TTL_TRADG_VOL": "200",
        "OPN_INTRST": "1200",
    }
    row = normalize_bse_fo_bhavcopy_row(raw)
    assert row is not None
    assert row["stock_code"] == "SENSEX"
    assert row["segment"] == "BFO"
    # BSE publishes no book post-reset: nothing may be invented from volume.
    assert "total_buy_qty" not in row
    assert "best_bid_price" not in row


def test_normalize_bse_fo_expiry_day_uses_last_traded_not_index_settlement():
    """Regression for the Day's P&L incident: on expiry day BSE `CLSPRIC` carries the
    underlying index level (settlement), not the option premium. `ltp` must come from
    `LastPric` so an OTM option prices at ~0.05, not ~76,391."""
    row = normalize_bse_fo_bhavcopy_row(
        {
            "TCKR_SYMB": "BSESEN",
            "XPRY_DT": "2026-07-23",
            "OPTN_TP": "PE",
            "STRK_PRIC": "74300",
            "CLSPRIC": "76391.39",  # index settlement value on expiry -- NOT the premium
            "LastPric": "0.05",     # real last traded option price
            "OPN_INTRST": "1200",
        }
    )
    assert row is not None
    assert row["ltp"] == "0.05"
