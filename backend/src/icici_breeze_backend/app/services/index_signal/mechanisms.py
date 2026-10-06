"""The fixed grid of signals: mechanism x version x duration x index (docs/signals-streamline-plan.md).

Two mechanisms, three durations, two indices, and -- for Momentum -- three versions run side by
side. Each series is identified by a `SeriesKey` and nothing else. There are no user-defined
variants: a series' evidence belongs to the exact definition that produced it, and a fixed grid is
what lets every bot and every backtest compare like with like.

A duration is both the window a mechanism reads and how long a call stands. A bot trading a
call holds it until the call ends; direction (follow or fade) is the bot's choice, not the
signal's.

Versions
--------
Changing a mechanism's formula makes a new version. Normally the new one replaces the old; the
2026-10-06 Momentum review instead runs v1, v2 and v3 together until v3 has been judged, so a
bot pins the version it trades (`SignalChoice.version`) and the 30-day gate is per version
(guide/technical/design-decisions.md #72). Every backtest run records the versions it replayed.

A series id names its version only when it is not the version the id meant before versions
coexisted (`LEGACY_VERSIONS`), so `nifty:momentum:15m` is still Momentum v2 and
`nifty:expansion:15m` still Volume expansion v3 -- every stored id, Redis key and saved bot keeps
meaning what it meant.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Union

from icici_breeze_backend.app.services.index_signal.expansion import (
    W_MIN_MINUTES,
    ExpansionEvaluator,
    ExpansionParams,
)
from icici_breeze_backend.app.services.index_signal.momentum import MomentumEvaluator, MomentumParams

MECHANISMS: tuple[str, ...] = ("expansion", "momentum")
DURATIONS: tuple[int, ...] = (1, 5, 15)
INDICES: tuple[str, ...] = ("nifty", "sensex")

# The navbar shows the chosen mechanism's 15-minute reading (decision 11).
NAVBAR_DURATION = 15

# Every version a mechanism currently runs, oldest first. Expansion: v2 timed readings at the
# bar's close and ended the session with the 15:14 bar; v3 ignores spikes after a dead minute.
# Momentum: v1 rebuilt its trend line daily, v2 carries it overnight, v3 adds the duration-fit
# volume test and the ATR minimum move (`momentum` module docstring).
VERSIONS: dict[str, tuple[int, ...]] = {"expansion": (3,), "momentum": (1, 2, 3)}
# What an id or a saved bot without a version means: the version that was current when versions
# started to coexist. Fixed history -- never edit it when a version is added or retired.
LEGACY_VERSIONS: dict[str, int] = {"expansion": 3, "momentum": 2}

# (mechanism, version, index) the grid does not run. SENSEX futures do not trade in about 47% of
# minutes, so after a quiet stretch any single trade out-ranks the baseline (#72).
WITHDRAWN: frozenset[tuple[str, int, str]] = frozenset({("momentum", 3, "sensex")})

# ICICI futures stock codes, and the exchange segment each one trades on.
STOCK_CODES: dict[str, str] = {"nifty": "NIFTY", "sensex": "BSESEN"}
EXCHANGES: dict[str, str] = {"nifty": "NFO", "sensex": "BFO"}
# ICICI serves no BSE open interest (#34), so SENSEX expansion runs on price and volume alone.
HAS_OI: dict[str, bool] = {"nifty": True, "sensex": False}
# SENSEX futures trade a median 20 contracts a minute and nothing in ~47% of minutes (#34).
THIN_DATA: dict[str, bool] = {"nifty": False, "sensex": True}

INDEX_NAMES: dict[str, str] = {"nifty": "NIFTY", "sensex": "SENSEX"}
MECHANISM_NAMES: dict[str, str] = {"expansion": "Volume expansion", "momentum": "Momentum"}

# Plain-language descriptions for the Signals page (decision 4: a layman must follow them).
MECHANISM_SUMMARIES: dict[str, str] = {
    "expansion": (
        "Fires when the index future moves unusually far on unusually heavy trading. On NIFTY it "
        "also needs open interest to be rising, so the move is backed by new positions rather "
        "than traders closing old ones."
    ),
    "momentum": (
        "Fires when the index future closes above (or below) both its recent trend line and the "
        "day's average traded price, on one of the heaviest bursts of trading of recent candles."
    ),
}
VERSION_SUMMARIES: dict[tuple[str, int], str] = {
    ("momentum", 1): (
        "The original rule. Its trend line is rebuilt from today's candles each morning, so it "
        "reads nothing until nine candles have closed (11:30 for the 15-minute reading). Kept for "
        "comparison until v3 has been judged."
    ),
    ("momentum", 2): (
        "Carries the trend line over from yesterday, shifted by the overnight gap, so it reads "
        "from the first candle. Volume must rank in the top fifth of the last 20 candles. Kept "
        "for comparison until v3 has been judged."
    ),
    ("momentum", 3): (
        "v2 with two stricter tests. Volume is judged for the job: the 1-minute reading needs a "
        "candle that out-trades each of the session's last three; the 5- and 15-minute readings "
        "need the top fifth of the same time of day over the last ten sessions, so a quiet "
        "midday is not compared with a busy open. And the close must clear the trend line by at "
        "least half an ATR, so a close a few paise past the line is not a call. NIFTY only."
    ),
}

# Momentum v3's slot baseline and how much of it must exist (MomentumParams).
SLOT_SESSIONS = 10
SLOT_MIN_SESSIONS = 8
# Sessions of history any series needs before today to read from its first candle: two for
# every rolling baseline (#41), the slot baseline's full ten for Momentum v3 at 5m/15m.
BASE_WARMUP_SESSIONS = 2
# Calendar days of bars before a range (or before today) that hold those sessions: ten sessions
# across two weekends and a holiday or two. Live warm-up and every backtest use the same span, so
# a live reading and its replay start from the same state.
WARMUP_CALENDAR_DAYS = 21


def latest_version(mechanism: str) -> int:
    return VERSIONS[mechanism][-1]


def versions_record() -> dict[str, list[int]]:
    """The versions a backtest replays, as stored on its run (JSON lists)."""
    return {m: list(v) for m, v in VERSIONS.items()}


def multi_version(mechanism: str) -> bool:
    """Whether names must say which version: only while more than one runs."""
    return len(VERSIONS[mechanism]) > 1


def options() -> list[tuple[str, int]]:
    """Every (mechanism, version) a bot may pick, in the order the page and comparisons list
    them: expansion first, then Momentum newest first."""
    out: list[tuple[str, int]] = []
    for m in MECHANISMS:
        out.extend((m, v) for v in reversed(VERSIONS[m]))
    return out


def version_name(mechanism: str, version: int) -> str:
    name = MECHANISM_NAMES[mechanism]
    return f"{name} v{version}" if multi_version(mechanism) else name


@dataclass(frozen=True, order=True)
class SeriesKey:
    mechanism: str
    duration: int
    index: str
    #: None means the legacy version (`LEGACY_VERSIONS`); resolved on construction.
    version: Optional[int] = None

    def __post_init__(self) -> None:
        if self.mechanism not in MECHANISMS:
            raise ValueError(f"unknown signal mechanism {self.mechanism!r}")
        if self.duration not in DURATIONS:
            raise ValueError(f"signal duration must be one of {DURATIONS}, not {self.duration!r}")
        if self.index not in INDICES:
            raise ValueError(f"unknown index {self.index!r}")
        if self.version is None:
            object.__setattr__(self, "version", LEGACY_VERSIONS[self.mechanism])
        if self.version not in VERSIONS[self.mechanism]:
            raise ValueError(
                f"{MECHANISM_NAMES[self.mechanism]} has no version {self.version!r} "
                f"(running: {', '.join(f'v{v}' for v in VERSIONS[self.mechanism])})"
            )

    @property
    def legacy(self) -> bool:
        return self.version == LEGACY_VERSIONS[self.mechanism]

    @property
    def id(self) -> str:
        """`nifty:expansion:15m`, `nifty:momentum:v3:15m` -- stable; used in Redis keys, file
        names and run records. The legacy version keeps the id it had before versions coexisted."""
        if self.legacy:
            return f"{self.index}:{self.mechanism}:{self.duration}m"
        return f"{self.index}:{self.mechanism}:v{self.version}:{self.duration}m"

    @property
    def slug(self) -> str:
        """`expansion-15m`, `momentum-v3-15m` -- the per-index folder name inside a backtest zip."""
        if self.legacy:
            return f"{self.mechanism}-{self.duration}m"
        return f"{self.mechanism}-v{self.version}-{self.duration}m"

    @property
    def published(self) -> bool:
        """False for a series the grid deliberately does not run (`WITHDRAWN`)."""
        return (self.mechanism, int(self.version), self.index) not in WITHDRAWN

    @property
    def uses_oi(self) -> bool:
        return self.mechanism == "expansion" and HAS_OI[self.index]

    @property
    def thin_data(self) -> bool:
        return THIN_DATA[self.index]

    @property
    def stock_code(self) -> str:
        return STOCK_CODES[self.index]

    @property
    def warmup_sessions(self) -> int:
        """Sessions before today this series needs to read from today's first candle."""
        if self.mechanism == "momentum" and momentum_params(self).volume_test == "slot":
            return SLOT_SESSIONS
        return BASE_WARMUP_SESSIONS

    @property
    def name(self) -> str:
        return f"{version_name(self.mechanism, int(self.version))} {self.duration}m · {INDEX_NAMES[self.index]}"

    @classmethod
    def parse(cls, raw: str) -> "SeriesKey":
        """`index:mechanism:Nm` (the legacy version) or `index:mechanism:vV:Nm`."""
        try:
            parts = str(raw).strip().lower().split(":")
            if len(parts) == 3:
                index, mechanism, duration = parts
                return cls(mechanism, int(duration.rstrip("m")), index)
            index, mechanism, version, duration = parts
            if not version.startswith("v"):
                raise ValueError(version)
            return cls(mechanism, int(duration.rstrip("m")), index, int(version[1:]))
        except (ValueError, AttributeError) as exc:
            raise ValueError(f"not a signal series id: {raw!r}") from exc

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "mechanism": self.mechanism,
            "duration": self.duration,
            "index": self.index,
            "version": self.version,
            "uses_oi": self.uses_oi,
            "thin_data": self.thin_data,
            "published": self.published,
            "name": self.name,
        }


def all_keys(*, include_withdrawn: bool = False) -> list[SeriesKey]:
    """Every series the grid runs (and, on request, the ones it deliberately does not)."""
    keys = [SeriesKey(m, d, i, v) for m, v in options() for d in DURATIONS for i in INDICES]
    return keys if include_withdrawn else [k for k in keys if k.published]


def keys_for(mechanism: str, duration: int, version: Optional[int] = None) -> list[SeriesKey]:
    return [SeriesKey(mechanism, duration, i, version) for i in INDICES]


def warmup_sessions(index: str) -> int:
    """Sessions before today the live engines of `index` are rebuilt from: the most any of its
    published series needs."""
    return max(k.warmup_sessions for k in all_keys() if k.index == index)


def expansion_params(key: SeriesKey) -> ExpansionParams:
    """The price/volume window is the duration; NIFTY's OI is read over at least 15 minutes,
    below which one-minute OI change is rounding error (#34)."""
    d = key.duration
    if not key.uses_oi:
        return ExpansionParams(window_minutes=d, require_oi=False, hold_minutes=d)
    oi_window = max(d, W_MIN_MINUTES)
    return ExpansionParams(
        window_minutes=d,
        oi_window_minutes=None if oi_window == d else oi_window,
        require_oi=True,
        hold_minutes=d,
    )


def momentum_params(key: SeriesKey) -> MomentumParams:
    """Each version's definition. Changing what a version decides means a new version."""
    d = key.duration
    if key.version == 1:
        return MomentumParams(candle_minutes=d, carry_ema=False)
    if key.version == 2:
        return MomentumParams(candle_minutes=d)
    # v3: the volume test fits the duration's job (the 1m reading drives the scalper's entries,
    # 5m/15m are context), plus the ATR minimum move. Same defaults at every duration until a
    # backtest says otherwise (#72).
    return MomentumParams(
        candle_minutes=d,
        volume_test="burst" if d == 1 else "slot",
        burst_lookback=3,
        slot_sessions=SLOT_SESSIONS,
        slot_min_sessions=SLOT_MIN_SESSIONS,
        atr_period=14,
        atr_fraction=0.5,
    )


def params_dict(key: SeriesKey) -> dict[str, Any]:
    """Every parameter of a series, for run records and the README in a backtest zip."""
    from dataclasses import asdict

    p = expansion_params(key) if key.mechanism == "expansion" else momentum_params(key)
    return {"mechanism": key.mechanism, "version": key.version, **asdict(p)}


Evaluator = Union[ExpansionEvaluator, MomentumEvaluator]


def make_evaluator(key: SeriesKey) -> Evaluator:
    if key.mechanism == "expansion":
        return ExpansionEvaluator(expansion_params(key))
    return MomentumEvaluator(momentum_params(key))
