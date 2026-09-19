"""Signal variants: named, pre-registered ways of reading the expansion mechanism (#38).

A variant is the expansion engine (#34) with its windows fixed, plus a direction:

    window_minutes     the price-and-volume window
    oi_window_minutes  the open-interest window, or None for price and volume alone
    hold_minutes       how long a call stands -- and so how long a bot acting on it holds
    direction          "follow" trades the call, "fade" trades against it

Each variant is published under its own Redis key, shadow-logged under its own label and judged
by the same fixed readiness test, so every one builds its own evidence. Bots choose a variant by
id; the bot never decides a window itself.

Why a variant cannot be edited, only created and deleted
--------------------------------------------------------
Evidence belongs to the exact parameters that produced it. Changing a window in place would
score readings taken under one definition as another's -- the silent re-definition #33's "one
fixed test, decided in advance" exists to prevent. So a changed definition is a new variant with
an empty record, and deleting one deletes its evidence. Two variants with identical parameters
are refused: they would be one signal counted twice.

Why NIFTY only
--------------
The expansion mechanism has a live bar source only for NIFTY futures. SENSEX futures trade too
thinly to carry a trade-based signal (#34), and there is no always-on SENSEX option feed yet.

The 15-minute follow variant is the incumbent
---------------------------------------------
It is exactly the mechanism the navbar already publishes for NIFTY, so it keeps that mechanism's
log label (`nifty:expansion`) and the month of evidence already under it, rather than starting a
duplicate series. It cannot be deleted.
"""
from __future__ import annotations

import re
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass
from typing import Any, Literal, Optional

import icici_breeze_backend.app.core.config as cfg
from icici_breeze_backend.app.services.index_signal.expansion import W_MIN_MINUTES, ExpansionParams

Direction = Literal["follow", "fade"]
DIRECTIONS: tuple[str, ...] = ("follow", "fade")
INDICES: tuple[str, ...] = ("nifty",)

INCUMBENT_LOG_LABEL = "nifty:expansion"
BACKTEST_SUFFIX = ":backtest"

# Bounds on a user-created variant. The OI floor is the engine's own (#34); the ceilings keep a
# window inside one trading session's worth of baseline.
WINDOW_MIN, WINDOW_MAX = 1, 60
OI_WINDOW_MIN, OI_WINDOW_MAX = W_MIN_MINUTES, 60
HOLD_MIN, HOLD_MAX = 1, 60
NAME_MAX = 60

_FLIP = {"bullish": "bearish", "bearish": "bullish"}


@dataclass(frozen=True)
class SignalVariant:
    id: str
    name: str
    index: str
    window_minutes: int
    oi_window_minutes: Optional[int]
    hold_minutes: int
    direction: Direction
    builtin: bool = False
    created_at: Optional[str] = None

    @property
    def requires_oi(self) -> bool:
        return self.oi_window_minutes is not None

    @property
    def incumbent(self) -> bool:
        return self.id == INCUMBENT_ID

    @property
    def log_label(self) -> str:
        return INCUMBENT_LOG_LABEL if self.incumbent else f"{self.index}:expansion:{self.id}"

    @property
    def backtest_label(self) -> str:
        return f"{self.log_label}{BACKTEST_SUFFIX}"

    def params(self) -> ExpansionParams:
        # An OI window equal to the price window is stored as None, so a variant with the
        # incumbent's windows compares equal to the incumbent's params and shares its engine.
        oi = self.oi_window_minutes
        return ExpansionParams(
            window_minutes=self.window_minutes,
            oi_window_minutes=None if oi is None or oi == self.window_minutes else oi,
            require_oi=self.requires_oi,
            hold_minutes=self.hold_minutes,
        )

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out.update(
            requires_oi=self.requires_oi,
            incumbent=self.incumbent,
            log_label=self.log_label,
            backtest_label=self.backtest_label,
        )
        return out


def variant_id(
    index: str, window_minutes: int, oi_window_minutes: Optional[int], hold_minutes: int, direction: str
) -> str:
    """Derived from the parameters, so a definition always has the same id and a duplicate is a
    primary-key clash rather than a second series."""
    oi = f"oi{oi_window_minutes}" if oi_window_minutes is not None else "nooi"
    return f"{index}-w{window_minutes}-{oi}-h{hold_minutes}-{direction}"


INCUMBENT_ID = variant_id("nifty", 15, 15, 15, "follow")
FADE_15_ID = variant_id("nifty", 15, 15, 15, "fade")

# The starting set (agreed 2026-09-19): the incumbent, its fade, and the two five-minute
# price/volume windows -- one still confirmed by 15 minutes of OI, one with no OI at all.
BUILTINS: tuple[SignalVariant, ...] = (
    SignalVariant(INCUMBENT_ID, "Expansion 15m · follow", "nifty", 15, 15, 15, "follow", True),
    SignalVariant(FADE_15_ID, "Expansion 15m · fade", "nifty", 15, 15, 15, "fade", True),
    SignalVariant(
        variant_id("nifty", 5, 15, 5, "follow"), "5m price/vol + 15m OI · follow",
        "nifty", 5, 15, 5, "follow", True,
    ),
    SignalVariant(
        variant_id("nifty", 5, None, 5, "follow"), "5m price/vol, no OI · follow",
        "nifty", 5, None, 5, "follow", True,
    ),
)

_ID_PATTERN = re.compile(r"^[a-z]+-w\d+-(oi\d+|nooi)-h\d+-(follow|fade)$")

_lock = threading.Lock()
_ensured_paths: set[str] = set()
_cache: dict[str, tuple[float, list[SignalVariant]]] = {}
_CACHE_SECONDS = 30.0


def _db_path() -> str:
    return cfg.DATA_PATH + cfg.USERS_DB


def ensure_variants_table(db_path: Optional[str] = None) -> None:
    path = db_path or _db_path()
    with _lock:
        if path in _ensured_paths:
            return
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS index_signal_variants (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                idx TEXT NOT NULL,
                window_minutes INTEGER NOT NULL,
                oi_window_minutes INTEGER,
                hold_minutes INTEGER NOT NULL,
                direction TEXT NOT NULL,
                builtin INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        # Built-ins cannot be deleted, so re-seeding on every start only ever fills a gap.
        conn.executemany(
            "INSERT OR IGNORE INTO index_signal_variants "
            "(id, name, idx, window_minutes, oi_window_minutes, hold_minutes, direction, builtin) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 1)",
            [
                (v.id, v.name, v.index, v.window_minutes, v.oi_window_minutes, v.hold_minutes, v.direction)
                for v in BUILTINS
            ],
        )
        conn.commit()
    with _lock:
        _ensured_paths.add(path)


def _row(r: sqlite3.Row) -> SignalVariant:
    return SignalVariant(
        id=str(r["id"]),
        name=str(r["name"]),
        index=str(r["idx"]),
        window_minutes=int(r["window_minutes"]),
        oi_window_minutes=None if r["oi_window_minutes"] is None else int(r["oi_window_minutes"]),
        hold_minutes=int(r["hold_minutes"]),
        direction="fade" if r["direction"] == "fade" else "follow",
        builtin=bool(r["builtin"]),
        created_at=r["created_at"],
    )


def list_variants(db_path: Optional[str] = None, *, fresh: bool = False) -> list[SignalVariant]:
    """Built-ins first, then user variants oldest first. Cached briefly: the publisher asks on
    every loop, and every write here clears the cache."""
    path = db_path or _db_path()
    now = time.monotonic()
    with _lock:
        hit = _cache.get(path)
    if hit is not None and not fresh and now - hit[0] < _CACHE_SECONDS:
        return list(hit[1])
    ensure_variants_table(path)
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM index_signal_variants ORDER BY builtin DESC, created_at ASC, id ASC"
        ).fetchall()
    out = [_row(r) for r in rows]
    # The built-ins in their declared order, not the table's.
    order = {v.id: i for i, v in enumerate(BUILTINS)}
    out.sort(key=lambda v: (order.get(v.id, len(order)), v.created_at or "", v.id))
    with _lock:
        _cache[path] = (now, out)
    return list(out)


def get_variant(variant: str, db_path: Optional[str] = None) -> Optional[SignalVariant]:
    key = str(variant or "").strip().lower()
    return next((v for v in list_variants(db_path) if v.id == key), None)


def is_variant_label(label: str) -> bool:
    """A variant's live or replay log label (the incumbent's is the mechanism's own)."""
    base = label[: -len(BACKTEST_SUFFIX)] if label.endswith(BACKTEST_SUFFIX) else label
    parts = base.split(":")
    return len(parts) == 3 and parts[1] == "expansion" and bool(_ID_PATTERN.match(parts[2]))


def _invalidate() -> None:
    with _lock:
        _cache.clear()


def create_variant(
    *,
    name: str,
    window_minutes: int,
    oi_window_minutes: Optional[int],
    hold_minutes: int,
    direction: str,
    index: str = "nifty",
    db_path: Optional[str] = None,
) -> SignalVariant:
    """Raises ValueError with a message fit for the screen."""
    index = str(index or "").strip().lower()
    if index not in INDICES:
        raise ValueError("Variants run on NIFTY only: it is the one index with a live futures bar feed.")
    name = str(name or "").strip()
    if not name or len(name) > NAME_MAX:
        raise ValueError(f"Give the variant a name of 1 to {NAME_MAX} characters.")
    if direction not in DIRECTIONS:
        raise ValueError("Direction must be follow or fade.")
    w, h = int(window_minutes), int(hold_minutes)
    if not WINDOW_MIN <= w <= WINDOW_MAX:
        raise ValueError(f"The price/volume window must be {WINDOW_MIN} to {WINDOW_MAX} minutes.")
    if not HOLD_MIN <= h <= HOLD_MAX:
        raise ValueError(f"The hold must be {HOLD_MIN} to {HOLD_MAX} minutes.")
    oi = None if oi_window_minutes is None else int(oi_window_minutes)
    if oi is not None and not OI_WINDOW_MIN <= oi <= OI_WINDOW_MAX:
        raise ValueError(
            f"The open-interest window must be {OI_WINDOW_MIN} to {OI_WINDOW_MAX} minutes: below "
            f"{W_MIN_MINUTES} the OI change is rounding error."
        )
    variant = SignalVariant(variant_id(index, w, oi, h, direction), name, index, w, oi, h, direction)  # type: ignore[arg-type]
    variant.params()  # the engine's own validation, so nothing is stored that it would refuse
    existing = get_variant(variant.id, db_path)
    if existing is not None:
        raise ValueError(f"“{existing.name}” already reads the signal this way.")
    path = db_path or _db_path()
    ensure_variants_table(path)
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO index_signal_variants "
            "(id, name, idx, window_minutes, oi_window_minutes, hold_minutes, direction, builtin) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 0)",
            (variant.id, name, index, w, oi, h, direction),
        )
        conn.commit()
    _invalidate()
    return get_variant(variant.id, db_path) or variant


def delete_variant(variant: str, db_path: Optional[str] = None) -> SignalVariant:
    """Delete a user variant and its evidence. Raises ValueError for a built-in or unknown id.

    The caller checks first that no bot is set to it: a bot left pointing at a deleted variant
    would read `unavailable` and simply never trade, with nothing saying why."""
    found = get_variant(variant, db_path)
    if found is None:
        raise ValueError("No such signal variant.")
    if found.builtin:
        raise ValueError("Built-in variants cannot be deleted.")
    path = db_path or _db_path()
    with sqlite3.connect(path) as conn:
        conn.execute("DELETE FROM index_signal_variants WHERE id = ?", (found.id,))
        conn.commit()
    _invalidate()
    from icici_breeze_backend.app.services.index_signal import shadow_log

    shadow_log.purge_variant_label(found.log_label)
    shadow_log.purge_variant_label(found.backtest_label)
    return found


def apply_direction(snapshot: dict[str, Any], variant: SignalVariant) -> dict[str, Any]:
    """The engine's reading, turned the variant's way and labelled with what it is.

    A fade swaps bullish and bearish and negates the strength, so the shadow report's
    correlation with the next move reads the traded direction, not the engine's. Everything
    that is not a call -- neutral, unavailable, and why -- passes through untouched."""
    out = dict(snapshot)
    state = str(snapshot.get("state") or "unavailable")
    out["source_state"] = state
    if variant.direction == "fade":
        out["state"] = _FLIP.get(state, state)
        strength = snapshot.get("signal")
        if isinstance(strength, (int, float)):
            out["signal"] = -float(strength)
    out.update(
        label=variant.log_label,
        mechanism="expansion",
        variant_id=variant.id,
        variant_name=variant.name,
        direction=variant.direction,
    )
    return out


def reset_state_for_tests() -> None:
    with _lock:
        _ensured_paths.clear()
        _cache.clear()
