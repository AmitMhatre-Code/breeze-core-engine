"""Shared scrip_master SQL helpers."""
from __future__ import annotations

import datetime


def _expiry_api_to_display(expiry: str) -> str:
    s = expiry.removesuffix("T06:00:00.000Z")
    fmt = "%Y-%m-%d" if len(s.split("-")[0]) == 4 else "%d-%b-%Y"
    return datetime.datetime.strptime(s, fmt).strftime("%d-%b-%Y")


def scrip_master_expiry_sql_values(expiry: str) -> tuple[str, ...]:
    """Deduped ExpiryDate forms for scrip_master SQL (display + ISO)."""
    s = str(expiry or "").strip()
    if not s:
        return ()
    display = s
    if "T" in s:
        try:
            display = _expiry_api_to_display(s)
        except ValueError:
            pass
    elif len(s) == 10 and s[4] == "-" and len(s.split("-")[0]) == 4:
        try:
            display = datetime.datetime.strptime(s, "%Y-%m-%d").strftime("%d-%b-%Y")
        except ValueError:
            pass
    keys: list[str] = []
    if display:
        keys.append(display)
    try:
        iso = datetime.datetime.strptime(display, "%d-%b-%Y").date().isoformat()
        if iso not in keys:
            keys.append(iso)
    except ValueError:
        pass
    return tuple(keys)


def normalize_expiry_display(expiry: str) -> str:
    """Canonical DD-Mon-YYYY display form for chain keys and tick matching."""
    values = scrip_master_expiry_sql_values(expiry)
    if values:
        return values[0]
    return str(expiry or "").strip()


def expiry_display_equivalent(a: str, b: str) -> bool:
    """True when two expiry strings refer to the same contract date."""
    va = set(scrip_master_expiry_sql_values(a))
    vb = set(scrip_master_expiry_sql_values(b))
    if not va or not vb:
        return str(a or "").strip() == str(b or "").strip()
    return bool(va & vb)
