"""The fixed grid of signals: mechanism x duration x index (docs/signals-streamline-plan.md).

Two mechanisms, three durations, two indices -- twelve series, each identified by a `SeriesKey`
and nothing else. There are no user-defined variants: a series' evidence belongs to the exact
definition that produced it, and a fixed grid is what lets every bot and every backtest compare
like with like.

A duration is both the window a mechanism reads and how long a call stands. A bot trading a
call holds it until the call ends; direction (follow or fade) is the bot's choice, not the
signal's.

Versions
--------
Each mechanism carries a version. Changing its formula bumps it, the version is stamped on every
backtest run, and the 30-day availability gate only counts runs on the current version -- so a
bot can never trade a definition that no backtest has seen.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Union

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

# v2 of expansion: readings are timed at the bar's close and the session ends with the 15:14
# bar. v1 (#34/#38) timed calls from the bar's start and read the 15:15 bar.
VERSIONS: dict[str, int] = {"expansion": 2, "momentum": 1}

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


@dataclass(frozen=True, order=True)
class SeriesKey:
    mechanism: str
    duration: int
    index: str

    def __post_init__(self) -> None:
        if self.mechanism not in MECHANISMS:
            raise ValueError(f"unknown signal mechanism {self.mechanism!r}")
        if self.duration not in DURATIONS:
            raise ValueError(f"signal duration must be one of {DURATIONS}, not {self.duration!r}")
        if self.index not in INDICES:
            raise ValueError(f"unknown index {self.index!r}")

    @property
    def id(self) -> str:
        """`nifty:expansion:15m` -- stable; used in Redis keys, file names and run records."""
        return f"{self.index}:{self.mechanism}:{self.duration}m"

    @property
    def slug(self) -> str:
        """`expansion-15m` -- the per-index folder name inside a backtest zip."""
        return f"{self.mechanism}-{self.duration}m"

    @property
    def version(self) -> int:
        return VERSIONS[self.mechanism]

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
    def name(self) -> str:
        return f"{MECHANISM_NAMES[self.mechanism]} {self.duration}m · {INDEX_NAMES[self.index]}"

    @classmethod
    def parse(cls, raw: str) -> "SeriesKey":
        try:
            index, mechanism, duration = str(raw).strip().lower().split(":")
            return cls(mechanism, int(duration.rstrip("m")), index)
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
            "name": self.name,
        }


def all_keys() -> list[SeriesKey]:
    return [SeriesKey(m, d, i) for m in MECHANISMS for d in DURATIONS for i in INDICES]


def keys_for(mechanism: str, duration: int) -> list[SeriesKey]:
    return [SeriesKey(mechanism, duration, i) for i in INDICES]


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
    return MomentumParams(candle_minutes=key.duration)


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
