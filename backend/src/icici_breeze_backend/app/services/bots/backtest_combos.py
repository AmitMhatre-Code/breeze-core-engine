"""The signal settings a bot backtest compares (docs/signals-streamline-plan.md section 8).

A bot that reads a signal is replayed once per cell of the signal grid, so one run answers
"which signal would have served this bot best?" rather than only "how did my setting do?". The
gate never applies here: comparing is the point, and the gate only decides what a bot may *trade*.

Every running version of a mechanism is its own setting (#72): Volume expansion, Momentum v3, v2
and v1 -- four signal choices while Momentum's versions run side by side.

* Bot 3 (momentum scalper): every signal choice x duration x follow/fade -- twenty-four replays.
* Bot 4 (iron fly): its settings without a signal filter (VIX filter kept if set), plus the fly
  held by each signal choice x duration's quiet test -- thirteen replays.
* CAS Bingo: its saved strategy on every signal choice x duration, and follow/fade for the debit
  spread (the credit spread reads flips as published) -- twenty-four or twelve; the long strangle
  reads no signal and is replayed once. Momentum v3 reads nothing on SENSEX, so a v3 setting
  trades NIFTY only.
* Bot 2 (expiry writer) reads no signal: one replay, as configured.

Bots 2, 3 and 4 also compare their premium gate (docs/premium-gate-plan.md): off, and at 0.90,
1.00 and 1.20, each at the bot's saved other settings -- one factor at a time, never crossed with
the signal grid, so the scalper compares 24 + 3 rows, not 96. The row matching the saved gate is
the saved row already listed, so it is not repeated; the others follow it.

Every other setting is the bot's saved one. The saved combination is marked, so the table can
say "your setting" beside the alternatives.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Optional

from icici_breeze_backend.app.domain.bots import SignalChoice
from icici_breeze_backend.app.services.index_signal.mechanisms import (
    DURATIONS,
    LEGACY_VERSIONS,
    options,
)

DIRECTIONS = ("follow", "fade")


@dataclass(frozen=True)
class Combo:
    id: str
    label: str
    config: Any
    signal: Optional[dict[str, Any]]
    is_saved: bool


def _choice_dict(choice: SignalChoice, *, direction: bool = True) -> dict[str, Any]:
    out = {"mechanism": choice.mechanism, "version": choice.version, "duration": choice.duration}
    if direction:
        out["direction"] = choice.direction
    return out


def _cell(m: str, v: int, d: int) -> str:
    """`momentum-v3-1m`; the legacy version keeps its pre-#72 id (`momentum-15m`)."""
    return f"{m}-{d}m" if v == LEGACY_VERSIONS[m] else f"{m}-v{v}-{d}m"


def _same_cell(choice: SignalChoice, m: str, v: int, d: int) -> bool:
    return choice.mechanism == m and choice.version == v and choice.duration == d


def combos_for(bot: str, config: Any) -> list[Combo]:
    combos = _signal_combos(bot, config)
    if bot in ("expiry", "fly", "momentum"):
        # Straight after the saved row's series (its follow and fade both): they read that
        # series, which the bounded readings cache (`backtest_service.KEEP_SERIES`) still holds
        # there and may not hold at the end.
        at = next((i + 1 for i, c in enumerate(combos) if c.is_saved), len(combos))
        while at < len(combos) and at > 0 and _cell_of(combos[at]) == _cell_of(combos[at - 1]):
            at += 1
        combos[at:at] = _premium_combos(bot, config)
    return combos


def _cell_of(combo: Combo) -> Optional[tuple]:
    """The signal series a combo reads, direction aside; None for one that reads none."""
    if not combo.signal:
        return None
    return tuple(sorted((k, v) for k, v in combo.signal.items() if k != "direction"))


def _premium_combos(bot: str, config: Any) -> list[Combo]:
    from icici_breeze_backend.app.services.premium_gate.replay import COMPARED_THRESHOLDS

    saved = config.premium_gate
    verb = "Buy at or below" if bot == "momentum" else "Sell at or above"
    out = []
    for threshold in (None, *COMPARED_THRESHOLDS):
        enabled = threshold is not None
        if enabled == saved.enabled and (not enabled or abs(threshold - saved.threshold) < 1e-9):
            continue  # the saved row is already in the table
        gate = saved.model_copy(update={"enabled": enabled, "threshold": threshold or saved.threshold})
        out.append(Combo(
            id=f"premium-{'off' if not enabled else f'{threshold:.2f}'}",
            label=f"Premium gate off" if not enabled else f"Premium gate: {verb.lower()} {threshold:.2f}x",
            config=config.model_copy(update={"premium_gate": gate}),
            signal=None,
            is_saved=False,
        ))
    return out


def _signal_combos(bot: str, config: Any) -> list[Combo]:
    if bot == "momentum":
        out = []
        for m, v in options():
            for d in DURATIONS:
                for direction in DIRECTIONS:
                    choice = SignalChoice(mechanism=m, version=v, duration=d, direction=direction)
                    out.append(Combo(
                        id=f"{_cell(m, v, d)}-{direction}",
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
        for m, v in options():
            for d in DURATIONS:
                choice = SignalChoice(mechanism=m, version=v, duration=d)
                out.append(Combo(
                    id=f"quiet-{_cell(m, v, d)}",
                    label=f"Only while {choice.label()} is quiet",
                    config=config.model_copy(update={
                        "entry_filter": saved.model_copy(update={"kind": "signal_quiet", "signal": choice})
                    }),
                    signal=_choice_dict(choice, direction=False),
                    is_saved=saved.kind == "signal_quiet" and _same_cell(saved.signal, m, v, d),
                ))
        return out
    if bot == "cas" and config.strategy in ("debit_spread", "credit_spread"):
        # The credit spread reads flips as published, so only the debit spread has a direction.
        directions = DIRECTIONS if config.strategy == "debit_spread" else ("follow",)
        out = []
        for m, v in options():
            for d in DURATIONS:
                for direction in directions:
                    choice = SignalChoice(mechanism=m, version=v, duration=d, direction=direction)
                    saved = config.signal
                    out.append(Combo(
                        id=f"{_cell(m, v, d)}-{direction}",
                        label=choice.label(),
                        config=config.model_copy(update={"signal": choice}),
                        signal=_choice_dict(choice, direction=config.strategy == "debit_spread"),
                        is_saved=(_same_cell(saved, m, v, d)
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
    end is what used to exhaust the container (guide/technical/design-decisions.md #41)."""
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
