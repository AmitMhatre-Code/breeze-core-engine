"""Underlying identity registry, built from ICICI's Security Master.

The bug these guard: a hand-kept name set in config classified `BSESEN` (ICICI's ShortName for
SENSEX) as a single stock, so index shorts were charged the 5% stock ELM tier instead of 2%.
"""
import sqlite3

import pytest

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.services.reference_data import symbol_registry as registry
from tests.fixtures.symbol_master import seed_symbol_master


@pytest.fixture
def seeded(tmp_path, monkeypatch):
    seed_symbol_master(tmp_path, monkeypatch)
    yield
    registry.clear_cache()


class TestClassification:
    def test_index_kind_from_instrument_name(self, seeded):
        for short_name in ("NIFTY", "CNXBAN", "NIFSEL", "BSESEN", "BANKEX"):
            assert registry.is_index(short_name) is True, short_name

    def test_single_stocks_are_not_index(self, seeded):
        for short_name in ("RELIND", "INFTEC"):
            assert registry.is_index(short_name) is False, short_name

    def test_unknown_underlying_is_none_not_false(self, seeded):
        """None is the whole point: a caller must not inherit "not an index" from "never heard
        of it" -- that is what charged the 5% stock ELM tier on SENSEX."""
        assert registry.is_index("NOSUCH") is None

    def test_bse_index_options_classify_as_index(self, seeded):
        """BSE spells its F&O instrument OPTIND/FUTIND, not OPTIDX."""
        assert registry.resolve("BSESEN").kind == registry.KIND_INDEX


class TestResolution:
    def test_resolves_from_every_security_master_spelling(self, seeded):
        for spelling in ("BSESEN", "SENSEX", "BSE SENSEX", "bsesen"):
            assert registry.resolve(spelling).short_name == "BSESEN", spelling

    def test_resolves_punctuation_and_space_insensitively(self, seeded):
        assert registry.resolve("NIFTY50").short_name == "NIFTY"
        assert registry.resolve("niftybank").short_name == "CNXBAN"

    def test_resolves_bhavcopy_feed_spellings(self, seeded):
        """NSE's derivative bhavcopy names index contracts its own way; no ICICI file carries
        those, so they are the one declared table in the registry."""
        assert registry.resolve("BANKNIFTY").short_name == "CNXBAN"
        assert registry.resolve("MIDCPNIFTY").short_name == "NIFSEL"

    def test_company_name_resolves_for_tick_stock_names(self, seeded):
        """breeze_connect stamps the SecurityMaster company name into a tick's stock_name."""
        assert registry.resolve("INFOSYS LTD").short_name == "INFTEC"
        assert registry.resolve("NIFTY BANK").short_name == "CNXBAN"

    def test_segment_filters(self, seeded):
        assert registry.resolve("BSESEN", segment=cfg.BFO).short_name == "BSESEN"
        assert registry.resolve("BSESEN", segment=cfg.NFO) is None

    def test_unknown_resolves_to_none(self, seeded):
        assert registry.resolve("NOSUCH") is None
        assert registry.resolve("") is None


class TestDerivedViews:
    def test_short_name_for_falls_back_to_input(self, seeded):
        assert registry.short_name_for("SENSEX") == "BSESEN"
        assert registry.short_name_for("NOSUCH") == "NOSUCH"

    def test_exchange_symbol_for(self, seeded):
        assert registry.exchange_symbol_for("CNXBAN") == "NIFTY BANK"
        assert registry.exchange_symbol_for("RELIND") == "RELIANCE"

    def test_aliases_cover_every_namespace_a_lookup_may_be_keyed_by(self, seeded):
        aliases = registry.aliases_for("CNXBAN")
        assert {"CNXBAN", "NIFTY BANK", "BANKNIFTY"} <= set(aliases)

    def test_aliases_bridge_stock_names_without_a_declared_table(self, seeded):
        """Stocks need no feed alias -- ICICI's ExchangeCode already is the exchange symbol."""
        assert {"RELIND", "RELIANCE"} <= set(registry.aliases_for("RELIANCE"))

    def test_underlyings_filters_by_segment_and_kind(self, seeded):
        nfo_indices = {s.short_name for s in registry.underlyings(cfg.NFO, kind=registry.KIND_INDEX)}
        assert nfo_indices == {"NIFTY", "CNXBAN", "NIFSEL"}
        bfo = {s.short_name for s in registry.underlyings(cfg.BFO)}
        assert bfo == {"BSESEN", "BANKEX"}

    def test_unresolved_bhavcopy_underlyings_reports_only_what_it_cannot_place(
        self, seeded, tmp_path
    ):
        """The bhavcopy names index contracts its own way, so a newly listed index shows up here
        as a spelling nothing resolves -- the check that would have caught NIFTYFPI."""
        conn = sqlite3.connect(str(tmp_path / cfg.SCRIP_DB))
        conn.execute("CREATE TABLE fo_bhavcopy (segment TEXT, stock_code TEXT)")
        conn.executemany(
            "INSERT INTO fo_bhavcopy VALUES (?,?)",
            [
                ("nfo", "BANKNIFTY"),  # bridged by a feed alias
                ("nfo", "RELIANCE"),  # bridged by the Security Master's own ExchangeCode
                ("bfo", "SENSEX"),
                ("nfo", "BRANDNEWIDX"),  # nothing knows this one
            ],
        )
        conn.commit()
        conn.close()
        assert registry.unresolved_bhavcopy_underlyings() == ("BRANDNEWIDX",)


class TestPreSymbolMasterDatabase:
    """An app upgraded onto a database written before symbol_master existed: scrip_master still
    carries the three names, so lookups keep working, but nothing carries InstrumentName."""

    @pytest.fixture
    def scrip_master_only(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cfg, "DATA_PATH", str(tmp_path) + "/")
        conn = sqlite3.connect(str(tmp_path / cfg.SCRIP_DB))
        conn.execute(
            "CREATE TABLE scrip_master (ShortName TEXT, CompanyName TEXT, ExchangeCode TEXT, SegmentCode TEXT)"
        )
        conn.executemany(
            "INSERT INTO scrip_master VALUES (?,?,?,?)",
            [("ADATRA", "ADANI ENERGY SOLUTIONS LIMITED", "ADANIENSOL", cfg.NFO)],
        )
        conn.commit()
        conn.close()
        registry.clear_cache()
        yield
        registry.clear_cache()

    def test_names_still_resolve(self, scrip_master_only):
        assert registry.exchange_symbol_for("ADATRA") == "ADANIENSOL"
        assert registry.short_name_for("ADANIENSOL") == "ADATRA"

    def test_classification_is_unknown_not_guessed(self, scrip_master_only):
        assert registry.is_index("ADATRA") is None

    def test_unclassified_rows_are_not_reported_as_a_populated_registry(self, scrip_master_only):
        """cache_bootstrap keys the startup master fetch on this: name-only rows must not read
        as a registry that knows anything about index-vs-stock."""
        assert registry.underlyings(kind=registry.KIND_INDEX) == ()
        assert registry.underlyings(kind=registry.KIND_STOCK) == ()


class TestEmptyRegistry:
    def test_reports_unknown_rather_than_guessing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cfg, "DATA_PATH", str(tmp_path) + "/")
        registry.clear_cache()
        try:
            assert registry.is_index("BSESEN") is None
            assert registry.resolve("BSESEN") is None
            # Declared feed spellings still map, so bhavcopy lookups work before a master load.
            assert registry.short_name_for("BANKNIFTY") == "CNXBAN"
        finally:
            registry.clear_cache()


class TestColdRegistryAliases:
    """Alias fan-out is how SPAN and bhavcopy lookups bridge namespaces, and it runs during
    ingest -- sometimes before any master load has populated the registry."""

    def test_reverse_feed_alias_without_a_populated_registry(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cfg, "DATA_PATH", str(tmp_path) + "/")
        registry.clear_cache()
        try:
            assert "BANKNIFTY" in registry.aliases_for("CNXBAN")
            assert "CNXBAN" in registry.aliases_for("BANKNIFTY")
        finally:
            registry.clear_cache()
