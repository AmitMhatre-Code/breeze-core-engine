"""Strike and expiry selection for condors: by delta on the listed grid, never by points."""
from __future__ import annotations

import datetime
from typing import Iterable, Optional

from icici_breeze_backend.app.domain.condor import CondorSettings
from icici_breeze_backend.app.services.condor.pricing import GreeksModel, Right


def _otm_of(right: Right, strike: float, bound: float) -> bool:
    """True when `strike` is at or beyond `bound` in the out-of-the-money direction."""
    return strike >= bound if right == "Call" else strike <= bound


def strike_for_delta(
    model: GreeksModel,
    strikes: Iterable[float],
    right: Right,
    target_abs_delta: float,
    *,
    not_inside: Optional[float] = None,
    outward: bool = False,
) -> Optional[float]:
    """The listed strike whose |delta| is nearest `target_abs_delta`.

    `not_inside` keeps the answer at or beyond that strike in the OTM direction: it is how a
    roll is capped at the tested strike. `outward` takes the nearest strike whose |delta| is
    at or below the target -- wings snap outward, so a wing is never closer than configured.
    Ties go to the strike further from the money. None when no strike can be priced.
    """
    best: Optional[tuple[float, float, float]] = None
    for strike in strikes:
        if not_inside is not None and not _otm_of(right, strike, not_inside):
            continue
        d = model.delta(right, strike)
        if d is None:
            continue
        a = abs(d)
        if outward and a > target_abs_delta + 1e-12:
            continue
        gap = abs(a - target_abs_delta)
        farness = -strike if right == "Put" else strike
        candidate = (gap, -farness, strike)
        if best is None or candidate < best:
            best = candidate
    return best[2] if best else None


def wing_at_width(strikes: Iterable[float], right: Right, short_strike: float, width: float) -> Optional[float]:
    """A wing `width` points beyond the short, snapped outward to the listed strikes.

    When the target lies past the furthest listed strike, the wing is that furthest strike: a
    narrower wing still caps the side, and no wing at all would leave the entry or roll undone
    (#69). None only when no strike lies beyond the short."""
    target = short_strike + width if right == "Call" else short_strike - width
    pool = list(strikes)
    if right == "Call":
        beyond = [s for s in pool if s >= target]
        if beyond:
            return min(beyond)
        past_short = [s for s in pool if s > short_strike]
        return max(past_short) if past_short else None
    beyond = [s for s in pool if s <= target]
    if beyond:
        return max(beyond)
    past_short = [s for s in pool if s < short_strike]
    return min(past_short) if past_short else None


def monthly_expiries(expiries: Iterable[datetime.date]) -> list[datetime.date]:
    """Each month's last listed expiry -- the designated monthly contract."""
    last: dict[tuple[int, int], datetime.date] = {}
    for e in expiries:
        key = (e.year, e.month)
        if key not in last or e > last[key]:
            last[key] = e
    return sorted(last.values())


def cycle_expiry(
    expiries: Iterable[datetime.date], today: datetime.date, settings: CondorSettings
) -> Optional[datetime.date]:
    """The expiry a new cycle should use: the earliest allowed one still at or above the
    tranche cut-off. If it is further out than the entry DTE, the tranche schedule simply
    waits until it comes into range."""
    pool = monthly_expiries(expiries) if settings.expiry_kind == "monthly" else sorted(set(expiries))
    for e in pool:
        if (e - today).days >= settings.tranche_cutoff_dte:
            return e
    return None


def tranche_due_dte(settings: CondorSettings, index: int) -> float:
    """DTE at or below which tranche `index` (0-based) is due: evenly spaced from the entry
    DTE, the last one a full step before the cut-off (45/40/35 for three tranches over
    45 -> 30). Due exactly *at* the cut-off, a tranche is missed whenever that DTE falls on a
    weekend or holiday -- the first replay lost every third tranche that way."""
    spacing = (settings.entry_dte - settings.tranche_cutoff_dte) / settings.tranches
    return settings.entry_dte - index * spacing
