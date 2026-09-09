"""Seed a symbol_master table the way processor.load_scrip_master builds it.

Rows are (Token, InstrumentName, ShortName, ExchangeCode, CompanyName) exactly as ICICI's
Security Master spells them -- InstrumentName is what makes an underlying an index (OPTIDX/FUTIDX
on NSE, OPTIND/FUTIND on BSE, against OPTSTK/FUTSTK).
"""
from __future__ import annotations

import sqlite3

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.services.reference_data import symbol_registry as registry

NSE_ROWS = [
    (1, "OPTIDX", "NIFTY", "NIFTY 50", "NIFTY 50"),
    (2, "FUTIDX", "NIFTY", "NIFTY 50", "NIFTY 50"),
    (3, "OPTIDX", "CNXBAN", "NIFTY BANK", "NIFTY BANK"),
    (4, "OPTIDX", "NIFSEL", "NIFTY MIDCAP", "NIFTY MIDCAP SELECT"),
    (5, "OPTSTK", "RELIND", "RELIANCE", "RELIANCE INDUSTRIES LTD"),
    (6, "OPTSTK", "INFTEC", "INFY", "INFOSYS LTD"),
    (7, "FUTSTK", "INFTEC", "INFY", "INFOSYS LTD"),
]
BSE_ROWS = [
    (101, "OPTIND", "BSESEN", "SENSEX", "BSE SENSEX"),
    (102, "FUTIND", "BSESEN", "SENSEX", "BSE SENSEX"),
    (103, "OPTIND", "BANKEX", "BANKEX", "BANKEX"),
]


def seed_symbol_master(tmp_path, monkeypatch, *, nse_rows=None, bse_rows=None) -> None:
    """Point cfg.DATA_PATH at tmp_path and build symbol_master there for both segments."""
    monkeypatch.setattr(cfg, "DATA_PATH", str(tmp_path) + "/")
    conn = sqlite3.connect(str(tmp_path / cfg.SCRIP_DB))
    try:
        cur = conn.cursor()
        segments = (
            (cfg.NFO, NSE_ROWS if nse_rows is None else nse_rows),
            (cfg.BFO, BSE_ROWS if bse_rows is None else bse_rows),
        )
        for exchange_code, rows in segments:
            cur.execute("DROP TABLE IF EXISTS raw_scrip_data")
            cur.execute(
                """
                CREATE TABLE raw_scrip_data (
                    Token INTEGER, InstrumentName TEXT, ShortName TEXT,
                    ExchangeCode TEXT, CompanyName TEXT
                )
                """
            )
            cur.executemany("INSERT INTO raw_scrip_data VALUES (?,?,?,?,?)", rows)
            registry.populate_symbol_master_from_raw(cur, exchange_code)
        conn.commit()
    finally:
        conn.close()
    registry.clear_cache()
