"""Portfolio SPAN scan from NSCCL risk arrays."""
from __future__ import annotations

import json
import sqlite3

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.services.nsccl_baseline import (
    _ingest_span_xml_stream,
    ensure_exchange_margin_baseline_table,
)
from icici_breeze_backend.app.services.reference_data.span_baseline_store import (
    publish_span_baseline_from_db,
)
from icici_breeze_backend.app.services.reference_data.span_portfolio_scan import (
    SpanLeg,
    compute_net_option_value,
    compute_portfolio_scanning_risk,
    compute_portfolio_span_margin,
)


def _ra(*values: float) -> list[float]:
    return list(values)


def test_portfolio_scanning_risk_below_sum_of_sell_standalone():
    """Hedged book: portfolio scan must be below sum of naked sell margins."""
    short_ra = _ra(
        100, 100, 500, 500, 500, 500, 100, 100, 100, 100, 100, 100, 100, 100, 100, 100
    )
    long_ra = _ra(
        50, 50, 100, 100, 100, 100, 50, 50, 50, 50, 50, 50, 50, 50, 50, 50
    )
    contracts = {
        "23950:PE": {
            "risk_array": short_ra,
            "margin_per_lot": 500.0 * 65,
            "lot_size": 65,
        },
        "23900:PE": {
            "risk_array": long_ra,
            "margin_per_lot": 100.0 * 65,
            "lot_size": 65,
        },
    }
    qty = 650
    legs = [
        SpanLeg(23950, "Put", "Sell", qty),
        SpanLeg(23900, "Put", "Buy", qty),
    ]
    scanning, warnings = compute_portfolio_scanning_risk(contracts, legs)
    assert not warnings
    assert scanning is not None
    assert scanning < 500.0 * 10  # well below naked short at worst scenario

    result = compute_portfolio_span_margin(
        contracts,
        legs,
        spot=24000.0,
        time_years=30 / 365,
        sigma=0.15,
    )
    assert result["found"] is True
    assert result["span_margin_required"] is not None
    assert result["scanning_risk"] is not None
    assert result["margin_benefit"] is not None
    assert result["margin_benefit"] > 0
    assert result["per_leg_standalone"]["0"] > 0
    assert result["per_leg_standalone"]["1"] == 0


def test_buy_legs_zero_standalone():
    contracts = {
        "24000:CE": {
            "risk_array": _ra(200.0) * 16,
            "margin_per_lot": 200.0 * 65,
            "lot_size": 65,
        },
    }
    legs = [SpanLeg(24000, "Call", "Buy", 65)]
    standalone = compute_portfolio_span_margin(
        contracts,
        legs,
        spot=24000.0,
        time_years=0.1,
    )
    assert standalone["per_leg_standalone"]["0"] == 0


def test_net_option_value_long_minus_short():
    legs = [
        SpanLeg(24000, "Call", "Sell", 65),
        SpanLeg(24100, "Call", "Buy", 65),
    ]
    nov = compute_net_option_value(legs, spot=24000.0, time_years=30 / 365, sigma=0.2)
    assert isinstance(nov, float)


def test_missing_risk_array_returns_warning():
    contracts = {
        "23500:CE": {"margin_per_lot": 1000.0, "lot_size": 75},
    }
    legs = [SpanLeg(23500, "Call", "Sell", 75)]
    scanning, warnings = compute_portfolio_scanning_risk(contracts, legs)
    assert scanning is None
    assert warnings


def test_ingest_span_xml_stores_risk_array(monkeypatch, tmp_path):
    monkeypatch.setattr(cfg, "DATA_PATH", str(tmp_path) + "/")
    monkeypatch.setattr(cfg, "SCRIP_DB", "scrips.sqlite3")
    db = tmp_path / "scrips.sqlite3"
    conn = sqlite3.connect(db)
    conn.execute(
        """
        CREATE TABLE scrip_master (
            ShortName TEXT, ExpiryDate TEXT, StrikePrice REAL, OptionType TEXT, LotSize INTEGER
        )
        """
    )
    conn.execute(
        "INSERT INTO scrip_master VALUES (?, ?, ?, ?, ?)",
        ("NIFTY", "26-Jun-2026", 23500, "CE", 75),
    )
    conn.commit()
    ensure_exchange_margin_baseline_table()

    xml = b"""<?xml version="1.0"?>
<spanFile>
  <oopPf>
    <pfCode>NIFTY</pfCode>
    <series>
      <pe>20260626</pe>
      <opt>
        <o>C</o>
        <k>23500</k>
        <ra>
          <a>-100</a><a>100</a><a>100</a><a>100</a>
          <a>100</a><a>100</a><a>100</a><a>100</a>
          <a>100</a><a>100</a><a>100</a><a>100</a>
          <a>100</a><a>100</a><a>100</a><a>100</a>
        </ra>
      </opt>
    </series>
  </oopPf>
</spanFile>
"""
    import io

    inserted, skipped = _ingest_span_xml_stream(
        conn,
        io.BytesIO(xml),
        exchange_code="NFO",
        source_file="test.xml",
        source_date="20260626",
        source_version=1,
        allowed_pf_codes=None,
    )
    assert inserted == 1
    assert skipped == 0
    row = conn.execute(
        "SELECT risk_array, margin_per_lot FROM exchange_margin_baseline WHERE strike_price = 23500"
    ).fetchone()
    assert row is not None
    ra = json.loads(row[0])
    assert len(ra) == 16
    assert row[1] == 100.0 * 75
    conn.close()

    from icici_breeze_backend.app.db.redis_client import get_redis

    get_redis()
    ver = publish_span_baseline_from_db()
    assert ver >= 1

    from icici_breeze_backend.app.services.reference_data.span_baseline_store import (
        get_span_baseline_sheet,
    )

    internal = get_span_baseline_sheet(
        "NFO", "NIFTY", "26-Jun-2026", include_risk_arrays=True
    )
    assert internal["found"] is True
    entry = internal["contracts"]["23500:CE"]
    assert len(entry["risk_array"]) == 16

    public = get_span_baseline_sheet("NFO", "NIFTY", "26-Jun-2026")
    pub_entry = public["contracts"]["23500:CE"]
    assert "risk_array" not in pub_entry
