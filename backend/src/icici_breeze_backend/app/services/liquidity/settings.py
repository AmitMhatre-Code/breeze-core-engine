"""The liquidity check's thresholds: one global row, edited in Settings -> Liquidity checks.

Global rather than per-user for the same reason as `pnl_engine_settings`: a deployment has one
trader (rarely two or three), and the bots and the Strategy Builder must judge a book the same
way the order tickets do. Read fresh on every check, so an edit applies to the next one. Never
an environment variable (#48's rule): a customer instance is provisioned by the portal's
CloudFormation stack, where an env-only knob is one nobody can turn.
"""
from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass

import icici_breeze_backend.app.core.config as cfg

DEFAULT_MAX_DEVIATION_PCT = 10.0
DEFAULT_MIN_DEVIATION_TICKS = 5
DEFAULT_LTP_STALE_SECONDS = 300

MAX_DEVIATION_PCT_BOUNDS = (0.5, 100.0)
MIN_DEVIATION_TICKS_BOUNDS = (0, 1000)
LTP_STALE_SECONDS_BOUNDS = (10, 3600)


@dataclass(frozen=True)
class LiquiditySettings:
    # A fill fails when it is further from the LTP than this share of the LTP...
    max_deviation_pct: float = DEFAULT_MAX_DEVIATION_PCT
    # ...and also further than this many ticks. Both must be exceeded, so a one-tick move on a
    # Rs 0.80 option (6%) does not warn.
    min_deviation_ticks: int = DEFAULT_MIN_DEVIATION_TICKS
    # A last trade older than this makes the LTP itself a poor benchmark.
    ltp_stale_seconds: int = DEFAULT_LTP_STALE_SECONDS

    def as_dict(self) -> dict[str, float | int]:
        return asdict(self)


def _db_path() -> str:
    return cfg.DATA_PATH + cfg.USERS_DB


def _clamp(value: float, bounds: tuple[float, float]) -> float:
    return max(bounds[0], min(bounds[1], value))


def ensure_liquidity_settings_table(db_path: str | None = None) -> None:
    with sqlite3.connect(db_path or _db_path()) as conn:
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS liquidity_settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                max_deviation_pct REAL NOT NULL DEFAULT {DEFAULT_MAX_DEVIATION_PCT},
                min_deviation_ticks INTEGER NOT NULL DEFAULT {DEFAULT_MIN_DEVIATION_TICKS},
                ltp_stale_seconds INTEGER NOT NULL DEFAULT {DEFAULT_LTP_STALE_SECONDS}
            )
            """
        )
        conn.execute("INSERT OR IGNORE INTO liquidity_settings (id) VALUES (1)")
        conn.commit()


def load_liquidity_settings() -> LiquiditySettings:
    try:
        ensure_liquidity_settings_table()
        with sqlite3.connect(_db_path()) as conn:
            row = conn.execute(
                "SELECT max_deviation_pct, min_deviation_ticks, ltp_stale_seconds "
                "FROM liquidity_settings WHERE id = 1"
            ).fetchone()
    except sqlite3.Error:
        row = None
    if not row:
        return LiquiditySettings()
    return LiquiditySettings(
        max_deviation_pct=_clamp(float(row[0]), MAX_DEVIATION_PCT_BOUNDS),
        min_deviation_ticks=int(_clamp(int(row[1]), MIN_DEVIATION_TICKS_BOUNDS)),
        ltp_stale_seconds=int(_clamp(int(row[2]), LTP_STALE_SECONDS_BOUNDS)),
    )


def save_liquidity_settings(
    *,
    max_deviation_pct: float | None = None,
    min_deviation_ticks: int | None = None,
    ltp_stale_seconds: int | None = None,
) -> LiquiditySettings:
    """Persist any of the three; an omitted one is left as it is. Raises ValueError outside
    the bounds (routes map it to a 422)."""
    checks = (
        ("max_deviation_pct", max_deviation_pct, MAX_DEVIATION_PCT_BOUNDS),
        ("min_deviation_ticks", min_deviation_ticks, MIN_DEVIATION_TICKS_BOUNDS),
        ("ltp_stale_seconds", ltp_stale_seconds, LTP_STALE_SECONDS_BOUNDS),
    )
    for name, value, (lo, hi) in checks:
        if value is not None and not (lo <= value <= hi):
            raise ValueError(f"{name} must be between {lo:g} and {hi:g}")
    current = load_liquidity_settings()
    updated = LiquiditySettings(
        max_deviation_pct=(
            float(max_deviation_pct) if max_deviation_pct is not None else current.max_deviation_pct
        ),
        min_deviation_ticks=(
            int(min_deviation_ticks) if min_deviation_ticks is not None else current.min_deviation_ticks
        ),
        ltp_stale_seconds=(
            int(ltp_stale_seconds) if ltp_stale_seconds is not None else current.ltp_stale_seconds
        ),
    )
    with sqlite3.connect(_db_path()) as conn:
        conn.execute(
            """
            INSERT INTO liquidity_settings (id, max_deviation_pct, min_deviation_ticks, ltp_stale_seconds)
            VALUES (1, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              max_deviation_pct = excluded.max_deviation_pct,
              min_deviation_ticks = excluded.min_deviation_ticks,
              ltp_stale_seconds = excluded.ltp_stale_seconds
            """,
            (updated.max_deviation_pct, updated.min_deviation_ticks, updated.ltp_stale_seconds),
        )
        conn.commit()
    return updated


def bounds() -> dict[str, float]:
    return {
        "max_deviation_pct_min": MAX_DEVIATION_PCT_BOUNDS[0],
        "max_deviation_pct_max": MAX_DEVIATION_PCT_BOUNDS[1],
        "min_deviation_ticks_min": MIN_DEVIATION_TICKS_BOUNDS[0],
        "min_deviation_ticks_max": MIN_DEVIATION_TICKS_BOUNDS[1],
        "ltp_stale_seconds_min": LTP_STALE_SECONDS_BOUNDS[0],
        "ltp_stale_seconds_max": LTP_STALE_SECONDS_BOUNDS[1],
    }
