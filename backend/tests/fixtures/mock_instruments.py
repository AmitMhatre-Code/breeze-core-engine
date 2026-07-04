"""Shared instrument catalog for the local Breeze mock server + test fixtures.

Both `tests/mock_breeze_server.py` (which decides what to stream for a given
subscribed token) and `tests/breeze_mock_env.py` (which seeds a real
`BreezeConnect` instance's internal `stock_script_dict_list` /
`token_script_dict_list` so `subscribe_feeds()`/tick-enrichment resolve
correctly) read from this single catalog, so client and server always agree
on what a given token represents.

Option tokens/prices intentionally match the real captured tick fixtures in
`tests/fixtures/icici_ticks/*.json` so on-the-wire shapes are directly
comparable to production-captured data.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Instrument:
    kind: str  # "equity" (wire length 21) | "option" (wire length 23)
    exchange_code: str  # "NSE" | "NFO" | "BFO"
    stock_code: str
    token: str
    company_name: str
    prev_close: float
    day_open: float
    day_high: float
    day_low: float
    last: float
    lot_size: int = 1
    expiry_date: str | None = None  # e.g. "30-Jun-2026", options only
    strike_price: str | None = None
    right: str | None = None  # "call" | "put", options only

    @property
    def contract_name(self) -> str:
        """Matches breeze_connect's internal `OPT-<stock>-<dd-Mon-YYYY>-<strike>-<CE|PE>` shape."""
        assert self.kind == "option"
        opt_suffix = "PE" if (self.right or "").lower() == "put" else "CE"
        return f"OPT-{self.stock_code}-{self.expiry_date}-{self.strike_price}-{opt_suffix}"


# NSE cash/index instruments -- "benchmark symbols" for OFF_MARKET bhavcopy simulation.
EQUITIES: list[Instrument] = [
    Instrument(
        kind="equity", exchange_code="NSE", stock_code="NIFTY", token="800001",
        company_name="NIFTY 50", prev_close=24738.15, day_open=24750.00,
        day_high=24895.30, day_low=24700.10, last=24812.40,
    ),
    Instrument(
        kind="equity", exchange_code="NSE", stock_code="BANKNIFTY", token="800002",
        company_name="NIFTY BANK", prev_close=55090.20, day_open=55150.00,
        day_high=55430.60, day_low=55010.05, last=55210.75,
    ),
    Instrument(
        kind="equity", exchange_code="NSE", stock_code="RELIANCE", token="800003",
        company_name="RELIANCE INDUSTRIES LTD", prev_close=1402.10, day_open=1404.00,
        day_high=1418.90, day_low=1397.50, last=1408.60, lot_size=1,
    ),
]

# NFO/BFO option instruments -- tokens/prices match tests/fixtures/icici_ticks/*.json.
OPTIONS: list[Instrument] = [
    Instrument(
        kind="option", exchange_code="NFO", stock_code="NIFTY", token="71472",
        company_name="NIFTY 50", prev_close=159.60, day_open=154.00,
        day_high=169.85, day_low=52.15, last=61.20, lot_size=25,
        expiry_date="30-Jun-2026", strike_price="24000", right="call",
    ),
    Instrument(
        kind="option", exchange_code="NFO", stock_code="NIFTY", token="71474",
        company_name="NIFTY 50", prev_close=2.00, day_open=1.50,
        day_high=1.70, day_low=1.10, last=1.40, lot_size=25,
        expiry_date="30-Jun-2026", strike_price="25000", right="call",
    ),
    Instrument(
        kind="option", exchange_code="NFO", stock_code="NIFTY", token="71475",
        company_name="NIFTY 50", prev_close=120.75, day_open=120.50,
        day_high=125.00, day_low=115.00, last=118.25, lot_size=25,
        expiry_date="30-Jun-2026", strike_price="25000", right="put",
    ),
    Instrument(
        kind="option", exchange_code="BFO", stock_code="SENSEX", token="820390",
        company_name="SENSEX", prev_close=350.95, day_open=410.00,
        day_high=410.00, day_low=158.15, last=227.35, lot_size=20,
        expiry_date="26-Jun-2026", strike_price="77000", right="call",
    ),
]

ALL_INSTRUMENTS: list[Instrument] = [*EQUITIES, *OPTIONS]

BY_TOKEN: dict[str, Instrument] = {inst.token: inst for inst in ALL_INSTRUMENTS}


def wire_exchange_prefix(instrument: Instrument) -> str:
    """Numeric exchange prefix used in the `<prefix>.<data_type>!<token>` wire symbol.

    Mirrors breeze_connect's own `get_stock_token_value` exchange_code_list:
    NSE/NFO -> "4.", BFO -> "8." for plain (non-OHLC) rate-refresh subscriptions.
    """
    if instrument.exchange_code == "BFO":
        return "8"
    return "4"
