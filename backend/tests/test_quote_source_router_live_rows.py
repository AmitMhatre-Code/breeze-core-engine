"""`rows_with_source` / `row_is_live`: the one definition of a live quote every bot shares."""
from __future__ import annotations

from icici_breeze_backend.app.services.quote_source_router import row_is_live, rows_with_source


def test_a_whole_websocket_chain_stamps_every_row_from_the_response():
    rows = rows_with_source({"Status": 200, "Success": [{"strike_price": 1}], "quote_source": "websocket"})
    assert rows == [{"strike_price": 1, "quote_source": "websocket"}]
    assert row_is_live(rows[0])


def test_a_cell_stamped_source_outranks_the_chains_dominant_one():
    """A mixed chain can be mostly websocket with a snapshot cell in it."""
    rows = rows_with_source(
        {"Success": [{"quote_source": "snapshot"}, {}], "quote_source": "websocket"}
    )
    assert [row_is_live(r) for r in rows] == [False, True]


def test_no_source_is_not_live():
    assert not row_is_live({})
    assert rows_with_source(None) == []
