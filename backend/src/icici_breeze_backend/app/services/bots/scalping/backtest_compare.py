"""Paper mode against the backtest, for the same days (docs/bots-scalping-plan.md section 8.9).

The one check that says whether the backtest can be believed. Paper mode fills against the
live bid/ask; the backtest fills against traded bars plus a modelled spread. Where both took
the same trade -- same contract, entries within a couple of minutes -- the difference in entry
price *is* the backtest's fill error, measured rather than assumed. Day totals are shown too,
but read them with care: once the two paths exit a trade at different moments, every later
trade can differ, so totals diverge for reasons that are not fill error.

Reads `bot_cycles` (paper rows only) from users.sqlite3 and never writes anything.
"""
from __future__ import annotations

import datetime
import json
import sqlite3
import statistics
from dataclasses import dataclass
from typing import Any, Optional, Sequence

import icici_breeze_backend.app.core.config as cfg

_TS = "%Y-%m-%d %H:%M:%S"
MATCH_TOLERANCE = datetime.timedelta(minutes=2)


@dataclass(frozen=True)
class PaperCycle:
    user_id: str
    opened_at: datetime.datetime
    closed_at: Optional[datetime.datetime]
    lots: int
    # Bot 3: the bought contract. Bot 4: the fly's centre, with `right` None.
    strike: Optional[float]
    right: Optional[str]
    # Bot 3: the fill price. Bot 4: net credit per unit.
    entry: Optional[float]
    gross_pnl: float
    friction: float
    net_pnl: float
    exit_reason: str
    aborted: bool = False


@dataclass(frozen=True)
class Pair:
    paper: PaperCycle
    backtest: Any  # BacktestCycle | FlyCycle

    @property
    def entry_diff(self) -> Optional[float]:
        mine = getattr(self.backtest, "entry_price", None)
        if mine is None:
            mine = getattr(self.backtest, "net_credit_per_unit", None)
        if mine is None or self.paper.entry is None:
            return None
        return round(float(mine) - float(self.paper.entry), 2)


def _users_db(db: Optional[str]) -> str:
    return db or (cfg.DATA_PATH + cfg.USERS_DB)


def _parse(raw: Any) -> Optional[datetime.datetime]:
    if not raw:
        return None
    try:
        return datetime.datetime.strptime(str(raw)[:19], _TS)
    except ValueError:
        return None


def load_paper_cycles(
    bot_type: str, day: datetime.date, *, user_id: Optional[str] = None, db: Optional[str] = None
) -> list[PaperCycle]:
    sql = (
        "SELECT user_id, opened_at, closed_at, lots, legs, detail, gross_pnl, friction, net_pnl, "
        "exit_reason_code FROM bot_cycles WHERE bot_type = ? AND paper = 1 AND DATE(opened_at) = ?"
    )
    args: list[Any] = [bot_type, day.isoformat()]
    if user_id:
        sql += " AND user_id = ?"
        args.append(user_id)
    sql += " ORDER BY opened_at ASC"
    with sqlite3.connect(_users_db(db)) as conn:
        rows = conn.execute(sql, args).fetchall()
    out = []
    for uid, opened, closed, lots, legs_raw, detail_raw, gross, friction, net, reason in rows:
        legs = json.loads(legs_raw or "[]")
        detail = json.loads(detail_raw or "{}") or {}
        opened_at = _parse(opened)
        if opened_at is None:
            continue
        if detail.get("atm_strike") is not None or detail.get("aborted"):
            strike, right = detail.get("atm_strike"), None
            entry = detail.get("net_credit_per_unit")
        else:
            leg = legs[0] if legs else {}
            strike, right = leg.get("strike_price"), leg.get("right")
            entry = (detail.get("entry") or {}).get("price")
        out.append(
            PaperCycle(
                user_id=str(uid),
                opened_at=opened_at,
                closed_at=_parse(closed),
                lots=int(lots or 0),
                strike=float(strike) if strike is not None else None,
                right=right,
                entry=float(entry) if entry is not None else None,
                gross_pnl=float(gross or 0.0),
                friction=float(friction or 0.0),
                net_pnl=float(net or 0.0),
                exit_reason=str(reason or ""),
                aborted=bool(detail.get("aborted")),
            )
        )
    return out


def run_config_hashes(bot_type: str, day: datetime.date, *, db: Optional[str] = None) -> set[Optional[str]]:
    """The config fingerprints the day's paper sessions ran under."""
    with sqlite3.connect(_users_db(db)) as conn:
        rows = conn.execute(
            "SELECT DISTINCT config_hash FROM bot_runs WHERE bot_type = ? AND DATE(started_at) = ?",
            (bot_type, day.isoformat()),
        ).fetchall()
    return {r[0] for r in rows}


def _contract(cycle: Any) -> tuple[Optional[float], Optional[str]]:
    if hasattr(cycle, "atm_strike"):
        return float(cycle.atm_strike), None
    return float(cycle.strike), cycle.right


def match_cycles(
    paper: Sequence[PaperCycle], backtest: Sequence[Any], *, tolerance: datetime.timedelta = MATCH_TOLERANCE
) -> tuple[list[Pair], list[PaperCycle], list[Any]]:
    """Greedy, in time order: each paper cycle takes the nearest unclaimed backtest cycle on
    the same contract within the tolerance. (pairs, unmatched paper, unmatched backtest)."""
    unclaimed = list(backtest)
    pairs: list[Pair] = []
    lonely: list[PaperCycle] = []
    for p in paper:
        if p.aborted:
            continue
        best, best_gap = None, None
        for b in unclaimed:
            strike, right = _contract(b)
            if strike != p.strike or right != p.right:
                continue
            gap = abs(b.entered_at - p.opened_at)
            if gap <= tolerance and (best_gap is None or gap < best_gap):
                best, best_gap = b, gap
        if best is None:
            lonely.append(p)
        else:
            unclaimed.remove(best)
            pairs.append(Pair(p, best))
    return pairs, lonely, unclaimed


def _label(p: PaperCycle) -> str:
    kind = ("CE" if p.right == "call" else "PE") if p.right else "fly"
    return f"{int(p.strike or 0)} {kind}"


def _totals(rows: Sequence[Any]) -> dict[str, float]:
    gross = sum(r.gross_pnl for r in rows)
    friction = sum(r.friction for r in rows)
    return {"cycles": len(rows), "gross_pnl": round(gross, 2), "friction": round(friction, 2),
            "net_pnl": round(gross - friction, 2)}


def compare_payload(
    day: datetime.date,
    paper: Sequence[PaperCycle],
    backtest_cycles: Sequence[Any],
    *,
    config_hash_now: Optional[str],
    config_hashes_then: set[Optional[str]],
    price_source: str,
    lots: Optional[int] = None,
) -> dict[str, Any]:
    """What `render` prints, as data for the Backtest page."""
    live = [p for p in paper if not p.aborted]
    pairs, lonely, extra = match_cycles(live, backtest_cycles)
    diffs = [abs(d) for d in (pair.entry_diff for pair in pairs) if d is not None]
    return {
        "day": day.isoformat(),
        "price_source": price_source,
        "settings_changed": bool(config_hashes_then) and config_hash_now not in config_hashes_then,
        "lots": lots,
        "paper": _totals(live),
        "backtest": _totals(backtest_cycles),
        "pairs": [
            {
                "contract": _label(pair.paper),
                "paper_at": pair.paper.opened_at.strftime("%H:%M:%S"),
                "backtest_at": pair.backtest.entered_at.strftime("%H:%M:%S"),
                "paper_entry": pair.paper.entry,
                "backtest_entry": getattr(pair.backtest, "entry_price", getattr(pair.backtest, "net_credit_per_unit", None)),
                "entry_diff": pair.entry_diff,
                "paper_exit": pair.paper.exit_reason,
                "backtest_exit": pair.backtest.exit_reason,
                "paper_net": round(pair.paper.net_pnl, 2),
                "backtest_net": round(pair.backtest.net_pnl, 2),
            }
            for pair in pairs
        ],
        "paper_only": [f"{p.opened_at:%H:%M} {_label(p)}" for p in lonely],
        "backtest_only": [f"{b.entered_at:%H:%M}" for b in extra],
        "median_abs_entry_diff": round(statistics.median(diffs), 2) if diffs else None,
    }


def render(
    day: datetime.date,
    paper: Sequence[PaperCycle],
    backtest_cycles: Sequence[Any],
    *,
    config_hash_now: Optional[str],
    config_hashes_then: set[Optional[str]],
    price_source: str,
) -> list[str]:
    lines = [f"=== {day} : paper vs backtest ({price_source}) ==="]
    if config_hashes_then and config_hash_now not in config_hashes_then:
        lines.append(
            "  WARNING: the bot's settings have changed since this paper session ran; the backtest "
            "uses today's settings, so the two describe different strategies."
        )
    live = [p for p in paper if not p.aborted]

    def totals(rows: Sequence[Any]) -> str:
        gross = sum(r.gross_pnl for r in rows)
        friction = sum(r.friction for r in rows)
        return f"{len(rows):3d} cycles  gross {gross:10,.2f}  friction {friction:9,.2f}  net {gross - friction:10,.2f}"

    lines.append(f"  paper    : {totals(live)}")
    lines.append(f"  backtest : {totals(backtest_cycles)}")

    pairs, lonely, extra = match_cycles(live, backtest_cycles)
    if pairs:
        lines.append("")
        lines.append("  matched trades (same contract, entries within 2 min):")
        lines.append("    paper  bt     contract          entry paper   bt    diff   exit paper / bt                   net paper / bt")
        for pair in pairs:
            p, b = pair.paper, pair.backtest
            contract = f"{int(p.strike or 0)} {('CE' if p.right == 'call' else 'PE') if p.right else 'fly'}"
            lines.append(
                f"    {p.opened_at:%H:%M}  {b.entered_at:%H:%M}  {contract:16s}  "
                f"{(p.entry or 0):9.2f} {getattr(b, 'entry_price', getattr(b, 'net_credit_per_unit', 0)):7.2f} "
                f"{(pair.entry_diff or 0):+6.2f}   {p.exit_reason[:16]:16s} / {b.exit_reason[:16]:16s} "
                f"{p.net_pnl:9,.0f} / {b.net_pnl:9,.0f}"
            )
        diffs = [abs(d) for d in (pair.entry_diff for pair in pairs) if d is not None]
        if diffs:
            lines.append(f"  median |entry difference|: {statistics.median(diffs):.2f} per unit over {len(diffs)} trades")
    if lonely:
        lines.append(f"  paper only    : {', '.join(f'{p.opened_at:%H:%M}' for p in lonely)}")
    if extra:
        lines.append(f"  backtest only : {', '.join(f'{b.entered_at:%H:%M}' for b in extra)}")
    return lines
