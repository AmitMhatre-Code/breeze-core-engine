"""Tests for the durable last-known-good quote snapshot.

Covers the capture/flush/reload round trip, the session-age gate, and the
per-cell source merge that replaced the old per-chain bhavcopy/REST fallback.
"""
from __future__ import annotations

import datetime as dt
from unittest.mock import MagicMock, patch

import pytest

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.db.quote_snapshot_migrate import ensure_quote_snapshot_table
from icici_breeze_backend.app.services import ws_quote_snapshot as snap

TRADING_DAY = dt.date(2026, 7, 20)
PREV_DAY = dt.date(2026, 7, 17)


def _cell(ltp: float, buy: int, sell: int, *, updated_at: float = 1_784_000_000.0) -> dict:
    return {
        "stock_code": "SENSEX",
        "strike_price": 82000.0,
        "right": "Put",
        "expiry_date": "30-Jul-2026",
        "ltp": ltp,
        "open_interest": 1200,
        "total_buy_qty": buy,
        "total_sell_qty": sell,
        "best_bid_price": ltp - 0.5,
        "best_offer_price": ltp + 0.5,
        "updated_at": updated_at,
    }


@pytest.fixture
def snapshot_db(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "DATA_PATH", str(tmp_path) + "/")
    monkeypatch.setattr(cfg, "SCRIP_DB", "scrips.sqlite3")
    ensure_quote_snapshot_table(str(tmp_path / "scrips.sqlite3"))
    return str(tmp_path / "scrips.sqlite3")


def _record_one(buy: int = 4500, sell: int = 3200, *, date: dt.date = TRADING_DAY) -> None:
    snap.record_cells(
        [(cfg.BFO, "SENSEX", "30-Jul-2026", 82000.0, "put", _cell(95.25, buy, sell))],
        trading_date=date,
    )


def test_record_and_read_round_trip():
    _record_one()
    with patch.object(snap, "latest_snapshot_trading_date", return_value=TRADING_DAY):
        cell = snap.snapshot_cell(cfg.BFO, "SENSEX", "30-Jul-2026", 82000.0, "put")
    assert cell is not None
    # The whole point: real BSE depth survives the exchange's post-close reset.
    assert cell["total_buy_qty"] == 4500
    assert cell["total_sell_qty"] == 3200
    assert cell["best_bid_price"] == 94.75


def test_snapshot_is_fresh_only_for_latest_concluded_session():
    _record_one(date=PREV_DAY)
    with patch.object(snap, "latest_snapshot_trading_date", return_value=TRADING_DAY):
        assert snap.snapshot_is_fresh(cfg.BFO) is False
        assert snap.snapshot_cell(cfg.BFO, "SENSEX", "30-Jul-2026", 82000.0, "put") is None
    with patch.object(snap, "latest_snapshot_trading_date", return_value=PREV_DAY):
        assert snap.snapshot_is_fresh(cfg.BFO) is True


def test_flush_and_reload_survives_redis_loss(snapshot_db):
    """A restart or Redis wipe must not cost the session's captured depth."""
    _record_one()
    assert snap.flush_snapshot_to_sqlite(TRADING_DAY) == 1

    from icici_breeze_backend.app.db.redis_client import cache_delete_pattern

    cache_delete_pattern("quotes:snapshot:*")
    with patch.object(snap, "latest_snapshot_trading_date", return_value=TRADING_DAY):
        assert snap.snapshot_cell(cfg.BFO, "SENSEX", "30-Jul-2026", 82000.0, "put") is None
        assert snap.load_snapshot_from_sqlite(TRADING_DAY) == 1
        restored = snap.snapshot_cell(cfg.BFO, "SENSEX", "30-Jul-2026", 82000.0, "put")
    assert restored is not None
    assert restored["total_buy_qty"] == 4500


def test_flush_is_idempotent(snapshot_db):
    _record_one()
    snap.flush_snapshot_to_sqlite(TRADING_DAY)
    _record_one(buy=5000)
    snap.flush_snapshot_to_sqlite(TRADING_DAY)

    import sqlite3

    with sqlite3.connect(snapshot_db) as conn:
        rows = conn.execute("SELECT COUNT(*) FROM ws_quote_snapshot").fetchone()[0]
    assert rows == 1  # upserted, not duplicated


def test_cell_depth_as_of_is_ist_iso():
    as_of = snap.cell_depth_as_of(_cell(95.25, 10, 10, updated_at=1_784_000_000.0))
    assert as_of is not None and "+05:30" in as_of
    assert snap.cell_depth_as_of({"updated_at": 0}) is None
    assert snap.cell_depth_as_of({}) is None


# --------------------------------------------------------------- per-cell merge


def _proc_stub() -> MagicMock:
    proc = MagicMock()
    proc._get_full_option_chain_icici_rest.return_value = {
        "Status": 200,
        "Success": {
            "chain_rows": [
                {
                    "strike_price": 82100.0,
                    "put": {
                        "strike_price": 82100.0,
                        "ltp": 88.0,
                        # BSE zeroes its book at close -- this is what REST returns.
                        "total_buy_qty": 0,
                        "total_sell_qty": 0,
                    },
                    "call": None,
                }
            ]
        },
    }
    return proc


def test_offline_chain_prefers_snapshot_over_bhavcopy_depth():
    """Snapshot depth must win: bhavcopy carries no order book at all."""
    from icici_breeze_backend.app.services import quote_source_router as router

    _record_one()
    bhav_cell = {"strike_price": 82000.0, "ltp": 95.25, "total_buy_qty": None}
    with (
        patch.object(snap, "latest_snapshot_trading_date", return_value=TRADING_DAY),
        patch.object(router, "offline_source_order", return_value=["snapshot", "bhavcopy"]),
        patch.object(router, "is_tradeable_contract", return_value=True),
        patch.object(router, "_lookup_bhav_row", return_value={"ltp": "95.25"}),
        patch.object(router, "_row_to_chain_cell", return_value=bhav_cell),
        patch.object(router, "get_bhavcopy_source_date", return_value=TRADING_DAY),
    ):
        payload = router._build_offline_chain(
            _proc_stub(),
            "u1",
            "SENSEX",
            cfg.BFO,
            "30-Jul-2026",
            [82000.0],
            lot_size=20,
            freeze_quantity=None,
        )
    assert payload is not None
    put_cell = payload["chain_rows"][0]["put"]
    assert put_cell["quote_source"] == "snapshot"
    assert put_cell["total_buy_qty"] == 4500
    assert put_cell["depth_as_of"] is not None
    assert payload["depth_as_of"] is not None


def test_offline_chain_falls_back_per_cell_not_per_chain():
    """A strike the snapshot missed still fills from the next tier, instead of
    the whole chain abandoning the snapshot."""
    from icici_breeze_backend.app.services import quote_source_router as router

    _record_one()
    bhav_cell = {"strike_price": 82100.0, "ltp": 88.0, "total_buy_qty": None}
    with (
        patch.object(snap, "latest_snapshot_trading_date", return_value=TRADING_DAY),
        patch.object(router, "offline_source_order", return_value=["snapshot", "bhavcopy"]),
        patch.object(router, "is_tradeable_contract", return_value=True),
        patch.object(router, "_lookup_bhav_row", return_value={"ltp": "88"}),
        patch.object(router, "_row_to_chain_cell", return_value=bhav_cell),
        patch.object(router, "get_bhavcopy_source_date", return_value=TRADING_DAY),
    ):
        payload = router._build_offline_chain(
            _proc_stub(),
            "u1",
            "SENSEX",
            cfg.BFO,
            "30-Jul-2026",
            [82000.0, 82100.0],
            lot_size=20,
            freeze_quantity=None,
        )
    assert payload is not None
    counts = payload["quote_source_counts"]
    assert counts["snapshot"] == 1  # the captured 82000 PUT
    assert counts["bhavcopy"] >= 1  # everything else filled in
