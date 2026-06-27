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
    assert int(row["total_buy_qty"]) > 0


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
