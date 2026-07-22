"""Tests for WebSocket token index lookup."""
from __future__ import annotations

import sqlite3

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.services.reference_data.ws_token_index import (
    clear_token_lookup_cache,
    exchange_from_ws_prefix,
    lookup_contract_by_token,
    lookup_contract_by_ws_symbol,
    lookup_token_for_contract,
    option_type_to_right,
    parse_ws_symbol,
)


def _init_ws_token_db(tmp_path, monkeypatch) -> str:
    monkeypatch.setattr(cfg, "DATA_PATH", str(tmp_path) + "/")
    monkeypatch.setattr(cfg, "SCRIP_DB", "scrips.sqlite3")
    db_path = cfg.DATA_PATH + cfg.SCRIP_DB
    with sqlite3.connect(db_path, timeout=30) as conn:
        conn.execute(
            """
            CREATE TABLE ws_token_index (
                Token INTEGER PRIMARY KEY,
                SegmentCode TEXT,
                ShortName TEXT,
                ExpiryDate DATE,
                StrikePrice REAL,
                OptionType TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO ws_token_index (Token, SegmentCode, ShortName, ExpiryDate, StrikePrice, OptionType)
            VALUES (820390, 'BFO', 'BSESEN', '2026-07-02', 77000, 'CE')
            """
        )
        conn.execute(
            """
            INSERT INTO ws_token_index (Token, SegmentCode, ShortName, ExpiryDate, StrikePrice, OptionType)
            VALUES (844663, 'BFO', 'BSESEN', '2026-07-02', 82000, 'CE')
            """
        )
    clear_token_lookup_cache()
    return db_path


def test_parse_ws_symbol():
    assert parse_ws_symbol("8.1!820390") == ("8.1", 820390)
    assert parse_ws_symbol("4.1!71474") == ("4.1", 71474)
    assert parse_ws_symbol("invalid") is None


def test_exchange_from_ws_prefix():
    assert exchange_from_ws_prefix("8.1") == cfg.BFO
    assert exchange_from_ws_prefix("4.1") == cfg.NFO
    assert exchange_from_ws_prefix("9.9") is None


def test_option_type_to_right():
    assert option_type_to_right("CE") == "call"
    assert option_type_to_right("PE") == "put"


def test_lookup_contract_by_ws_symbol(monkeypatch, tmp_path):
    _init_ws_token_db(tmp_path, monkeypatch)
    contract = lookup_contract_by_ws_symbol("8.1!820390")
    assert contract is not None
    assert contract.exchange_code == cfg.BFO
    assert contract.stock_code == "BSESEN"
    assert contract.expiry_display == "02-Jul-2026"
    assert contract.strike_price == 77000.0
    assert contract.option_type == "CE"


def test_lookup_contract_by_token_segment_filter(monkeypatch, tmp_path):
    _init_ws_token_db(tmp_path, monkeypatch)
    assert lookup_contract_by_token(820390, cfg.BFO) is not None
    assert lookup_contract_by_token(820390, cfg.NFO) is None


def test_populate_ws_token_index_canonicalizes_call_and_expiry(monkeypatch, tmp_path):
    from icici_breeze_backend.app.services.reference_data.ws_token_index import (
        lookup_token_for_contract,
        populate_ws_token_index_from_raw,
    )

    monkeypatch.setattr(cfg, "DATA_PATH", str(tmp_path) + "/")
    monkeypatch.setattr(cfg, "SCRIP_DB", "scrips.sqlite3")
    db_path = cfg.DATA_PATH + cfg.SCRIP_DB
    with sqlite3.connect(db_path, timeout=30) as conn:
        conn.execute(
            """
            CREATE TABLE raw_scrip_data (
                Token INTEGER PRIMARY KEY,
                ShortName TEXT,
                Series TEXT,
                ExpiryDate TEXT,
                StrikePrice REAL,
                OptionType TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO raw_scrip_data (Token, ShortName, Series, ExpiryDate, StrikePrice, OptionType)
            VALUES (820390, 'BSESEN', 'OPTION', '2026-07-02', 77000, 'Call')
            """
        )
        populate_ws_token_index_from_raw(conn.cursor(), cfg.BFO)
        row = conn.execute(
            "SELECT ExpiryDate, OptionType FROM ws_token_index WHERE Token = 820390"
        ).fetchone()
        conn.commit()
    assert row == ("02-Jul-2026", "CE")
    clear_token_lookup_cache()
    assert lookup_token_for_contract(cfg.BFO, "BSESEN", "02-Jul-2026", 77000.0, "Call") == 820390


def test_populate_ws_token_index_from_raw(monkeypatch, tmp_path):
    from icici_breeze_backend.app.services.reference_data.ws_token_index import (
        populate_ws_token_index_from_raw,
    )

    monkeypatch.setattr(cfg, "DATA_PATH", str(tmp_path) + "/")
    monkeypatch.setattr(cfg, "SCRIP_DB", "scrips.sqlite3")
    db_path = cfg.DATA_PATH + cfg.SCRIP_DB
    with sqlite3.connect(db_path, timeout=30) as conn:
        conn.execute(
            """
            CREATE TABLE raw_scrip_data (
                Token INTEGER PRIMARY KEY,
                InstrumentName TEXT,
                ShortName TEXT,
                Series TEXT,
                ExpiryDate DATE,
                StrikePrice REAL,
                OptionType TEXT,
                CALevel INTEGER,
                PermittedToTrade INTEGER,
                IssueCapital INTEGER,
                WarningQty INTEGER,
                FreezeQty INTEGER,
                CreditRating TEXT,
                NormalMarketStatus INTEGER,
                OddLotMarketStatus INTEGER,
                SpotMarketStatus INTEGER,
                AuctionMarketStatus INTEGER,
                NormalMarketEligibility TEXT,
                OddLotMarketEligibility TEXT,
                SpotMarketEligibility TEXT,
                AuctionMarketEligibility TEXT,
                IssueRate INTEGER,
                IssueStartDate DATE,
                InterestPaymentDate DATE,
                IssueMaturityDate DATE,
                MarginPercentage INTEGER,
                MinimumLotQty INTEGER,
                LotSize INTEGER,
                TickSize INTEGER,
                CompanyName TEXT,
                ListingDate DATE,
                ExpulsionDate DATE,
                ReAdmissionDate DATE,
                RecordDate DATE,
                LowPriceRange REAL,
                HighPriceRange REAL,
                SecurityExpiryDate DATE,
                NoDeliveryStartDate DATE,
                NoDeliveryEndDate DATE,
                MF TEXT,
                AON TEXT,
                ParticipantInMarketIndex TEXT,
                BookClsStartDate DATE,
                BookClsEndDate DATE,
                ExcerciseStartDate DATE,
                ExcerciseEndDate DATE,
                OldToken INTEGER,
                AssetInstrument TEXT,
                AssetName TEXT,
                AssetToken INTEGER,
                IntrinsicValue INTEGER,
                ExtrinsicValue INTEGER,
                ExcerciseStyle TEXT,
                EGM TEXT,
                AGM TEXT,
                Interest TEXT,
                Bonus TEXT,
                Rights TEXT,
                Dividends TEXT,
                ExAllowed TEXT,
                ExRejectionAllowed TEXT,
                PlAllowed TEXT,
                IsThisAsset TEXT,
                IsCorpAdjusted TEXT,
                LocalUpdateDatetime TEXT,
                DeleteFlag TEXT,
                Remarks TEXT,
                BasePrice INTEGER,
                ExchangeCode TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO raw_scrip_data (
                Token, ShortName, Series, ExpiryDate, StrikePrice, OptionType, ExchangeCode
            ) VALUES (820390, 'BSESEN', 'OPTION', '2026-07-02', 77000, 'CE', 'BSXOPT')
            """
        )
        populate_ws_token_index_from_raw(conn.cursor(), cfg.BFO)
        conn.commit()
    clear_token_lookup_cache()
    contract = lookup_contract_by_token(820390, cfg.BFO)
    assert contract is not None
    assert contract.stock_code == "BSESEN"
    assert contract.strike_price == 77000.0
    assert contract.expiry_display == "02-Jul-2026"
