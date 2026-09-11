"""Tests for the index signal's constituent weights: exchange parsing, storage, fallbacks."""
from __future__ import annotations

import zlib
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from icici_breeze_backend.app.core.timezone import IST
from icici_breeze_backend.app.services.index_signal import weights
from icici_breeze_backend.app.services.reference_data import symbol_registry

# Verbatim shape of text pulled from niftyindices' NIFTY 50 factsheet (2026-09).
FACTSHEET_TEXT = (
    "Sector Representation Sector Weight(%) Financial Services 36.47 Companys Name Weight(%) "
    "Top constituents by weightage HDFC Bank Ltd. 9.85 ICICI Bank Ltd. 9.45 "
    "Reliance Industries Ltd. 7.83 Bharti Airtel Ltd. 5.00 Larsen & Toubro Ltd. 4.30 "
    "State Bank of India 3.98 Infosys Ltd. 3.61 Axis Bank Ltd. 3.39 "
    "Kotak Mahindra Bank Ltd. 2.80 Mahindra & Mahindra Ltd. 2.66 "
    "# QTD,YTD and 1 year returns are absolute returns."
)
CONSTITUENTS_CSV = """Company Name,Industry,Symbol,Series,ISIN Code
HDFC Bank Ltd.,Financial Services,HDFCBANK,EQ,INE040A01034
ICICI Bank Ltd.,Financial Services,ICICIBANK,EQ,INE090A01021
Reliance Industries Ltd.,Energy,RELIANCE,EQ,INE002A01018
Bharti Airtel Ltd.,Telecommunication,BHARTIARTL,EQ,INE397D01024
Larsen & Toubro Ltd.,Construction,LT,EQ,INE018A01030
State Bank of India,Financial Services,SBIN,EQ,INE062A01020
Infosys Ltd.,Information Technology,INFY,EQ,INE009A01021
Axis Bank Ltd.,Financial Services,AXISBANK,EQ,INE238A01034
Kotak Mahindra Bank Ltd.,Financial Services,KOTAKBANK,EQ,INE237A01028
Mahindra & Mahindra Ltd.,Automobile,M&M,EQ,INE101A01026
"""
HEATMAP_BODY = (
    "bseindia$#$MARUTI,-5.12,MARUTI,0.00,13547.00,12637.60,12700.00,-685.50,532500,148.41,"
    "114523.00,/stock-share-price/maruti-suzuki-india-ltd/maruti/532500/,-5.12|"
    "M&M,-4.91,M&M,0.00,3370.15,3140.00,3168.90,-163.50,500520,170.41,530257.00,"
    "/stock-share-price/mahindra--mahindra-ltd/mm/500520/,-4.91"
)


@pytest.fixture(autouse=True)
def _reset():
    weights.reset_state_for_tests()
    yield
    weights.reset_state_for_tests()


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = str(tmp_path / "users_test.sqlite3")
    monkeypatch.setattr(weights, "_db_path", lambda: path)
    return path


def _nse_payload(n: int = 50) -> dict:
    data = [{"priority": 1, "symbol": "NIFTY 50", "ffmc": 0}]
    data += [{"priority": 0, "symbol": f"s{i:02d}", "ffmc": float(n - i)} for i in range(n)]
    return {"data": data}


def _pdf_with_text(*literals: str) -> bytes:
    content = "BT " + " ".join(f"({s}) Tj" for s in literals) + " ET"
    stream = zlib.compress(content.encode("latin-1"))
    return b"%PDF-1.4\n1 0 obj\n<< /Filter /FlateDecode >>\nstream\n" + stream + b"\nendstream\nendobj\n"


class _Resp:
    def __init__(self, *, status_code=200, content=b"", text="", payload=None):
        self.status_code = status_code
        self.content = content
        self.text = text
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class TestParsing:
    def test_nse_payload_weights_by_free_float_and_skips_the_index_row(self):
        rows = weights.parse_nse_index_payload(_nse_payload())
        assert len(rows) == 50
        assert rows[0][0] == "S00"
        assert sum(w for _s, w in rows) == pytest.approx(100.0)
        assert all(symbol != "NIFTY 50" for symbol, _w in rows)

    def test_nse_payload_with_too_few_constituents_is_rejected(self):
        with pytest.raises(weights.WeightsFetchError):
            weights.parse_nse_index_payload(_nse_payload(20))

    def test_bse_heatmap_rows(self):
        assert weights.parse_bse_heatmap(HEATMAP_BODY) == [("MARUTI", "532500"), ("M&M", "500520")]
        assert weights.parse_bse_heatmap("") == []
        assert weights.parse_bse_heatmap({"not": "a string"}) == []

    def test_sensex_weights_pass_the_index_checksum(self):
        caps = [(f"S{i}", 100.0) for i in range(30)]
        rows = weights.compute_sensex_weights(caps, 3000.0)
        assert rows[0][1] == pytest.approx(100.0 / 30)

    def test_sensex_weights_fail_a_partial_fetch(self):
        caps = [(f"S{i}", 100.0) for i in range(30)]
        with pytest.raises(weights.WeightsFetchError, match="free-float sum"):
            weights.compute_sensex_weights(caps, 3100.0)
        with pytest.raises(weights.WeightsFetchError, match="constituents"):
            weights.compute_sensex_weights(caps[:20], 2000.0)

    def test_factsheet_top_constituents(self):
        rows = weights.parse_factsheet_top_constituents(FACTSHEET_TEXT)
        assert len(rows) == 10
        assert rows[0] == ("HDFC Bank Ltd.", 9.85)
        assert rows[4] == ("Larsen & Toubro Ltd.", 4.30)
        assert rows[-1] == ("Mahindra & Mahindra Ltd.", 2.66)

    def test_extract_pdf_text_reads_flate_text_literals(self):
        text = weights.extract_pdf_text(_pdf_with_text("Top constituents by weightage", "HDFC Bank Ltd.", "9.85"))
        assert "Top constituents by weightage HDFC Bank Ltd. 9.85" in text

    def test_factsheet_source_maps_company_names_to_symbols(self):
        pdf = _pdf_with_text(FACTSHEET_TEXT)

        class _Session:
            def get(self, url, **_kw):
                if url == weights.NIFTY_FACTSHEET_URL:
                    return _Resp(content=pdf)
                return _Resp(text=CONSTITUENTS_CSV)

        rows = weights.fetch_nifty_weights_factsheet(session=_Session())
        assert rows[0] == ("HDFCBANK", 9.85)
        assert ("LT", 4.30) in rows
        assert ("M&M", 2.66) in rows

    def test_bse_bot_wall_html_is_a_fetch_error(self):
        class _Session:
            def get(self, url, **_kw):
                return _Resp(text="<html>error</html>")  # 200, but not JSON

        with pytest.raises(weights.WeightsFetchError, match="did not return JSON"):
            weights.fetch_sensex_weights_bse(session=_Session())


class TestStorage:
    def test_save_and_load_roundtrip_replaces_the_whole_set(self, db_path):
        weights.save_weights("nifty", [("A", 60.0), ("B", 40.0)], source="nse_api", as_of="2026-09-10")
        weights.save_weights("nifty", [("C", 100.0)], source="nse_api", as_of="2026-09-11")
        stored = weights.load_weights("nifty")
        assert stored is not None
        assert stored.rows == (("C", 100.0),)
        assert stored.as_of == "2026-09-11"

    def test_current_weight_set_falls_back_to_seeds(self, db_path):
        ws = weights.current_weight_set("sensex")
        assert ws.source == "seed"
        assert ws.as_of == weights.SEED_AS_OF
        assert ws.rows[0][0] == "HDFCBANK"

    def test_save_bumps_the_generation(self, db_path):
        before = weights.weights_generation()
        weights.save_weights("nifty", [("A", 100.0)], source="t", as_of="2026-09-10")
        assert weights.weights_generation() == before + 1

    def test_tracked_constituents_skips_unresolved_names_and_fills_from_below(self, db_path, monkeypatch):
        short = {"HDFCBANK": "HDFBAN", "ICICIBANK": "ICIBAN", "RELIANCE": "RELIND"}
        monkeypatch.setattr(
            symbol_registry,
            "resolve",
            lambda code, segment=None: SimpleNamespace(short_name=short[code]) if code in short else None,
        )
        weights.save_weights(
            "nifty",
            [("HDFCBANK", 10.0), ("NOTFNO", 9.0), ("ICICIBANK", 8.0), ("RELIANCE", 7.0)],
            source="nse_api",
            as_of="2026-09-10",
        )
        constituents, meta = weights.tracked_constituents("nifty", 2)
        assert [c.short_name for c in constituents] == ["HDFBAN", "ICIBAN"]
        assert meta["unresolved"] == ["NOTFNO"]
        assert meta["source"] == "nse_api"


class TestRefresh:
    def test_falls_through_to_the_factsheet_when_nse_fails(self, db_path, monkeypatch):
        def _boom():
            raise weights.WeightsFetchError("blocked")

        monkeypatch.setattr(weights, "fetch_nifty_weights_nse", _boom)
        monkeypatch.setattr(weights, "fetch_nifty_weights_factsheet", lambda: [("HDFCBANK", 9.85)])
        status = weights.refresh_weights("nifty")
        assert status["ok"] is True
        assert status["source"] == "niftyindices_factsheet"
        assert status["errors"] == ["nse_api: blocked"]
        assert weights.load_weights("nifty").source == "niftyindices_factsheet"

    def test_all_sources_failing_saves_nothing(self, db_path, monkeypatch):
        def _boom():
            raise weights.WeightsFetchError("blocked")

        monkeypatch.setattr(weights, "fetch_sensex_weights_bse", _boom)
        status = weights.refresh_weights("sensex")
        assert status["ok"] is False
        assert weights.load_weights("sensex") is None
        assert weights.current_weight_set("sensex").source == "seed"

    def test_weights_due(self, db_path, monkeypatch):
        from icici_breeze_backend.app.services import market_calendar

        monkeypatch.setattr(market_calendar, "is_trading_day", lambda *a, **k: True)
        now = datetime(2026, 9, 11, 10, 0, tzinfo=IST)
        assert weights.weights_due("nifty", now) is True  # nothing stored

        weights.save_weights(
            "nifty", [("A", 100.0)], source="t", as_of="2026-09-11", fetched_at=now.timestamp() - 60
        )
        assert weights.weights_due("nifty", now) is False  # fetched today

        yesterday = (now - timedelta(days=1)).timestamp()
        weights.save_weights("nifty", [("A", 100.0)], source="t", as_of="2026-09-10", fetched_at=yesterday)
        assert weights.weights_due("nifty", now) is True

        monkeypatch.setattr(market_calendar, "is_trading_day", lambda *a, **k: False)
        assert weights.weights_due("nifty", now) is False  # holidays keep the last trading day's set

    def test_failed_attempt_is_throttled(self, db_path, monkeypatch):
        def _boom():
            raise weights.WeightsFetchError("blocked")

        monkeypatch.setattr(weights, "fetch_sensex_weights_bse", _boom)
        now = datetime(2026, 9, 11, 10, 0, tzinfo=IST)
        weights.refresh_weights("sensex", now=now.timestamp())
        assert weights.weights_due("sensex", now + timedelta(minutes=5)) is False
        assert weights.weights_due("sensex", now + timedelta(minutes=31)) is True
