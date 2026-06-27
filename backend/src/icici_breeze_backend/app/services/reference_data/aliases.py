"""App/bhavcopy ticker aliases ↔ ICICI scrip_master ShortName."""

from __future__ import annotations

_SCRIP_SHORT_FOR_UNDERLYING: dict[str, str] = {
    "BANKNIFTY": "CNXBAN",
    "NIFTY BANK": "CNXBAN",
    "FINNIFTY": "NIFFIN",
    "NIFTY FINANCIAL": "NIFFIN",
    "NIFTY FINANCIAL SERVICES": "NIFFIN",
    "NIFTY 50": "NIFTY",
    "NIFTY50": "NIFTY",
    "SENSEX": "BSESEN",
}


def scrip_short_name(stock_code: str) -> str:
    sym = str(stock_code or "").strip().upper()
    return _SCRIP_SHORT_FOR_UNDERLYING.get(sym, sym)


def underlying_aliases(stock_code: str) -> tuple[str, ...]:
    sym = str(stock_code or "").strip().upper()
    if not sym:
        return ()
    aliases = {sym, scrip_short_name(sym)}
    for app_code, short_name in _SCRIP_SHORT_FOR_UNDERLYING.items():
        if sym in {app_code, short_name}:
            aliases.add(app_code)
            aliases.add(short_name)
    return tuple(sorted(aliases))
