"""The signal settings a bot backtest compares (docs/signals-streamline-plan.md section 8).

A bot that reads a signal is replayed once per cell of the signal grid, so one run answers
"which signal would have served this bot best?" rather than only "how did my setting do?". The
gate never applies here: comparing is the point, and the gate only decides what a bot may *trade*.

* Bot 3 (momentum scalper): every mechanism x duration x follow/fade -- twelve replays.
* Bot 4 (iron fly): its settings without a signal filter (VIX filter kept if set), plus the fly
  held by each mechanism x duration's quiet test -- seven replays.
* CAS Bingo: its saved strategy on every mechanism x duration, and follow/fade for the debit
  spread (the credit spread reads flips as published) -- twelve or six; the long strangle reads
  no signal and is replayed once.
* Bot 2 (expiry writer) reads no signal: one replay, as configured.

Every other setting is the bot's saved one. The saved combination is marked, so the table can
say "your setting" beside the alternatives.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Optional

from icici_breeze_backend.app.domain.bots import SignalChoice
from icici_breeze_backend.app.services.index_signal.mechanisms import DURATIONS, MECHANISMS

DIRECTIONS = ("follow", "fade")


@dataclass(frozen=True)
class Combo:
    id: str
    label: str
    config: Any
    signal: Optional[dict[str, Any]]
    is_saved: bool


def _choice_dict(choice: SignalChoice, *, direction: bool = True) -> dict[str, Any]:
    out = {"mechanism": choice.mechanism, "duration": choice.duration}
    if direction:
        out["direction"] = choice.direction
    return out


def combos_for(bot: str, config: Any) -> list[Combo]:
    if bot == "momentum":
        out = []
        for m in MECHANISMS:
            for d in DURATIONS:
                for direction in DIRECTIONS:
                    choice = SignalChoice(mechanism=m, duration=d, direction=direction)
                    out.append(Combo(
                        id=f"{m}-{d}m-{direction}",
                        label=choice.label(),
                        config=config.model_copy(update={"signal": choice}),
                        signal=_choice_dict(choice),
                        is_saved=config.signal == choice,
                    ))
        return out
    if bot == "fly":
        saved = config.entry_filter
        base_kind = "none" if saved.kind == "signal_quiet" else saved.kind
        base_label = "No signal filter" + (" (VIX filter on)" if base_kind == "vix_not_rising" else "")
        out = [Combo(
            id="no-signal-filter",
            label=base_label,
            config=config.model_copy(update={"entry_filter": saved.model_copy(update={"kind": base_kind})}),
            signal=None,
            is_saved=saved.kind != "signal_quiet",
        )]
        for m in MECHANISMS:
            for d in DURATIONS:
                choice = SignalChoice(mechanism=m, duration=d)
                out.append(Combo(
                    id=f"quiet-{m}-{d}m",
                    label=f"Only while {choice.label()} is quiet",
                    config=config.model_copy(update={
                        "entry_filter": saved.model_copy(update={"kind": "signal_quiet", "signal": choice})
                    }),
                    signal=_choice_dict(choice, direction=False),
                    is_saved=(saved.kind == "signal_quiet" and saved.signal.mechanism == m
                              and saved.signal.duration == d),
                ))
        return out
    if bot == "cas" and config.strategy in ("debit_spread", "credit_spread"):
        # The credit spread reads flips as published, so only the debit spread has a direction.
        directions = DIRECTIONS if config.strategy == "debit_spread" else ("follow",)
        out = []
        for m in MECHANISMS:
            for d in DURATIONS:
                for direction in directions:
                    choice = SignalChoice(mechanism=m, duration=d, direction=direction)
                    saved = config.signal
                    out.append(Combo(
                        id=f"{m}-{d}m-{direction}",
                        label=choice.label(),
                        config=config.model_copy(update={"signal": choice}),
                        signal=_choice_dict(choice, direction=config.strategy == "debit_spread"),
                        is_saved=(saved.mechanism == m and saved.duration == d
                                  and (config.strategy != "debit_spread" or saved.direction == direction)),
                    ))
        return out
    return [Combo(id="as-configured", label="As configured (reads no signal)", config=config,
                  signal=None, is_saved=True)]


def _exit_day(trade: dict[str, Any]) -> Optional[str]:
    for key in ("exited_at", "exit_at", "day", "entered_at"):
        raw = trade.get(key)
        if raw:
            return str(raw)[:10]
    return None


def daily_rows(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    days: dict[str, dict[str, Any]] = defaultdict(lambda: {"trades": 0, "wins": 0, "net_pnl": 0.0,
                                                           "gross_pnl": 0.0, "friction": 0.0})
    for t in trades:
        day = _exit_day(t)
        if day is None:
            continue
        d = days[day]
        net = float(t.get("net_pnl") or 0.0)
        d["trades"] += 1
        d["wins"] += 1 if net > 0 else 0
        d["net_pnl"] += net
        d["gross_pnl"] += float(t.get("gross_pnl") or 0.0)
        d["friction"] += float(t.get("friction") or 0.0)
    out = []
    running = 0.0
    for day in sorted(days):
        d = days[day]
        running += d["net_pnl"]
        out.append({"date": day, **{k: round(v, 2) if isinstance(v, float) else v for k, v in d.items()},
                    "cumulative_net_pnl": round(running, 2)})
    return out


def comparison_row(combo: Combo, summary: dict[str, Any], trades: list[dict[str, Any]]) -> dict[str, Any]:
    """One line of the comparison table: what this setting would have done."""
    ordered = sorted(trades, key=lambda t: str(t.get("exited_at") or t.get("entered_at") or ""))
    peak = running = drawdown = 0.0
    for t in ordered:
        running += float(t.get("net_pnl") or 0.0)
        peak = max(peak, running)
        drawdown = min(drawdown, running - peak)
    days = daily_rows(trades)
    return {
        "id": combo.id,
        "label": combo.label,
        "signal": combo.signal,
        "is_saved": combo.is_saved,
        "trades": int(summary.get("cycles") or len(trades)),
        "win_rate_pct": summary.get("win_rate_pct"),
        "gross_pnl": summary.get("gross_pnl"),
        "friction": summary.get("friction"),
        "net_pnl": summary.get("net_pnl"),
        "max_drawdown": round(drawdown, 2),
        "worst_day": min((d["net_pnl"] for d in days), default=None),
        "best_day": max((d["net_pnl"] for d in days), default=None),
        "days_replayed": summary.get("days_replayed"),
        "days_awaiting_data": summary.get("days_awaiting_data"),
    }


def headline(period_text: str, rows: list[dict[str, Any]]) -> str:
    saved = next((r for r in rows if r["is_saved"]), rows[0] if rows else None)
    if saved is None:
        return f"{period_text}: nothing replayed."
    text = (f"{period_text}: your setting ({saved['label']}) made {saved['trades']} trade(s), "
            f"net ₹{float(saved['net_pnl'] or 0):,.0f} after costs.")
    if len(rows) > 1:
        best = max(rows, key=lambda r: float(r["net_pnl"] or 0))
        if best is not saved:
            text += f" Best of {len(rows)} signal settings: {best['label']}, net ₹{float(best['net_pnl'] or 0):,.0f}."
        else:
            text += f" It was the best of {len(rows)} signal settings compared."
    waiting = max((int(r.get("days_awaiting_data") or 0) for r in rows), default=0)
    if waiting:
        text += f" {waiting} day(s) had no price data and were skipped."
    return text


README = """Bot backtest -- what is in this zip
=====================================

The bot was replayed on real ICICI prices over the period, once per signal setting it can use,
with every other setting as saved. Times are IST; money is rupees after trading costs.

run.json            the run: bot, period, dates, saved settings, sizing basis, notes
summary.csv         one row per signal setting: trades, win rate, gross, costs, net, the deepest
                    drawdown, the worst and best day. "is_saved" marks your current setting.
<setting>/trades.csv     every trade: entry and exit time, strike(s), lots, prices, costs, net P&L,
                         and why it closed
<setting>/daily.csv      per day: trades, wins, net P&L and the running total
<setting>/decisions.csv  every minute the bot was flat: what the gates decided, and the signal
                         reading it saw (state, reason, strength, when the call began)
audit.jsonl         the run's full trail, as the Activity log records it

A setting's signal is replayed exactly as the Signals page's backtest replays it, so the calls
here are the calls in a signal backtest's calls.csv for the same series.
"""


def decisions_member(combo: Combo) -> str:
    """Where one setting's minute-by-minute decisions go in the zip.

    Written by the caller as each setting finishes rather than returned from `zip_members`:
    decisions are by far the biggest thing a run produces, and holding every setting's until the
    end is what used to exhaust the container (docs/design-decisions.md #41)."""
    return f"{combo.id}/decisions.csv"


def zip_members(
    run: dict[str, Any],
    results: list[tuple[Combo, dict[str, Any], list[dict[str, Any]]]],
    rows: list[dict[str, Any]],
    trail: list[dict[str, Any]],
) -> dict[str, Any]:
    """{path in zip: rows (CSV) or text} for the run's zip, `decisions.csv` excepted.

    Each setting's `decisions.csv` is added separately, as that setting finishes -- see
    `decisions_member`."""
    import json

    members: dict[str, Any] = {
        "README.txt": README,
        "run.json": json.dumps({k: v for k, v in run.items() if k not in ("trades", "combos")},
                               indent=2, default=str),
        "summary.csv": [
            {**{k: v for k, v in r.items() if k != "signal"},
             **{f"signal_{k}": v for k, v in (r.get("signal") or {}).items()}}
            for r in rows
        ],
        "audit.jsonl": "\n".join(json.dumps(r, default=str, separators=(",", ":")) for r in trail) + "\n",
    }
    for combo, _summary, trades in results:
        members[f"{combo.id}/trades.csv"] = trades or [{"note": "no trades"}]
        members[f"{combo.id}/daily.csv"] = daily_rows(trades) or [{"note": "no trades"}]
    return members
