"""The settings combinations a condor backtest compares (design-decisions #71).

Every card backtest replays the full cross-product of five settings around the saved ones, so
one run answers "how do my rules compare with their neighbours?" as well as "how did my settings
do?". Agreed with the user, 2026-10-05:

* net-delta band per lot: saved ± 0.05
* minimum roll credit: saved ± 10 points
* max loss: the % of ceiling form × 0.5 / × 1 / × 1.5, a rupee limit left as saved; with no %
  form set, the rupee limit is scaled instead
* no-roll window: 0 and 3 days, plus the saved value if it is neither
* exit action: time roll and close

Plus the premium gate (#78), one factor at a time as the other gated bots have it (#75): off and at
0.90 / 1.00 / 1.20, each at the saved settings and exit action, less the row matching the saved
gate. Three more rows, not a fourth axis: crossed, the grid would be 432.

Everything else -- strikes, the cycle clock, the leg rule, sizing -- stays as saved: those either
change which contracts are traded (and so the fetch bill) or only scale P&L. A step outside a
setting's allowed range is dropped, never shifted inward, so a row is always the saved value or
exactly one step from it. The saved combination is always in the grid and marked.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from typing import Any, Optional

from pydantic import ValidationError

from icici_breeze_backend.app.domain.condor import CondorSettings

EXIT_ACTIONS = ("time_roll", "close")
BAND_STEP = 0.05
ROLL_CREDIT_STEP = 10.0
MAX_LOSS_FACTORS = (0.5, 1.0, 1.5)
NO_ROLL_WINDOWS = (0, 3)
EXIT_LABELS = {"time_roll": "Time roll", "close": "Close"}


@dataclass(frozen=True)
class Combo:
    id: str
    label: str
    settings: CondorSettings
    exit_action: str
    # The five values this combination sets, for the comparison table's columns.
    varied: dict[str, Any]
    # The settings it changes from the saved ones. Stored on its row in place of all of its
    # settings: the bot's evidence lookup reads every run's rows, so they are kept small.
    overrides: dict[str, Any]
    is_saved: bool


def _valid(saved: CondorSettings, field: str, value: Any) -> bool:
    try:
        CondorSettings(**{**saved.model_dump(), field: value})
    except ValidationError:
        return False
    return True


def _values(saved: CondorSettings, field: str, candidates: list[Any]) -> list[Any]:
    """The candidates the field accepts, saved value included, each once, in ascending order."""
    keep = {v for v in candidates if _valid(saved, field, v)}
    keep.add(getattr(saved, field))
    return sorted(keep)


def max_loss_field(saved: CondorSettings) -> str:
    """The max-loss form the grid varies: the % form, or the rupee one when only it is set."""
    return "max_loss_pct_of_ceiling" if saved.max_loss_pct_of_ceiling is not None else "max_loss_inr"


def _max_loss_text(field: str, value: float) -> str:
    return f"{value:g}%" if field == "max_loss_pct_of_ceiling" else f"₹{value:,.0f}"


def combos_for(saved: CondorSettings, saved_exit_action: str) -> list[Combo]:
    band = _values(saved, "net_delta_band_per_lot", [
        round(saved.net_delta_band_per_lot + d, 4) for d in (-BAND_STEP, BAND_STEP)
    ])
    credit = _values(saved, "min_roll_credit_points", [
        round(saved.min_roll_credit_points + d, 2) for d in (-ROLL_CREDIT_STEP, ROLL_CREDIT_STEP)
    ])
    loss_field = max_loss_field(saved)
    loss_saved = float(getattr(saved, loss_field))
    loss_round = 4 if loss_field == "max_loss_pct_of_ceiling" else 0
    loss = _values(saved, loss_field, [round(loss_saved * f, loss_round) for f in MAX_LOSS_FACTORS])
    no_roll = _values(saved, "no_roll_within_days_of_exit", list(NO_ROLL_WINDOWS))

    out: list[Combo] = []
    for b, c, m, n, x in product(band, credit, loss, no_roll, EXIT_ACTIONS):
        settings = CondorSettings(**{
            **saved.model_dump(),
            "net_delta_band_per_lot": b,
            "min_roll_credit_points": c,
            loss_field: m,
            "no_roll_within_days_of_exit": n,
        })
        is_saved = (
            b == saved.net_delta_band_per_lot and c == saved.min_roll_credit_points
            and m == loss_saved and n == saved.no_roll_within_days_of_exit and x == saved_exit_action
        )
        out.append(Combo(
            id=f"band{b:g}-credit{c:g}-loss{m:g}-noroll{n}-{x}",
            label=(
                f"Band {b:g} · Roll credit {c:g} · Max loss {_max_loss_text(loss_field, m)} · "
                f"No-roll {n}d · {EXIT_LABELS[x]}"
            ),
            settings=settings,
            exit_action=x,
            varied={
                "net_delta_band_per_lot": b,
                "min_roll_credit_points": c,
                "max_loss": _max_loss_text(loss_field, m),
                "no_roll_within_days_of_exit": n,
                "exit_action": x,
                "premium_gate": _gate_text(saved.premium_gate.enabled, saved.premium_gate.threshold),
            },
            overrides={
                "net_delta_band_per_lot": b,
                "min_roll_credit_points": c,
                loss_field: m,
                "no_roll_within_days_of_exit": n,
            },
            is_saved=is_saved,
        ))
    return out + premium_combos(saved, saved_exit_action)


def _gate_text(enabled: bool, threshold: float) -> str:
    return f"{threshold:.2f}x" if enabled else "off"


def premium_combos(saved: CondorSettings, saved_exit_action: str) -> list[Combo]:
    from icici_breeze_backend.app.services.premium_gate.replay import COMPARED_THRESHOLDS

    gate = saved.premium_gate
    out = []
    for threshold in (None, *COMPARED_THRESHOLDS):
        enabled = threshold is not None
        if enabled == gate.enabled and (not enabled or abs(threshold - gate.threshold) < 1e-9):
            continue
        new_gate = {"enabled": enabled, "threshold": threshold if enabled else gate.threshold}
        out.append(Combo(
            id=f"premium-{'off' if not enabled else f'{threshold:.2f}'}",
            label="Premium gate off" if not enabled else f"Premium gate: sell at or above {threshold:.2f}x",
            settings=CondorSettings(**{**saved.model_dump(), "premium_gate": new_gate}),
            exit_action=saved_exit_action,
            varied={
                "net_delta_band_per_lot": saved.net_delta_band_per_lot,
                "min_roll_credit_points": saved.min_roll_credit_points,
                "max_loss": _max_loss_text(max_loss_field(saved), float(getattr(saved, max_loss_field(saved)))),
                "no_roll_within_days_of_exit": saved.no_roll_within_days_of_exit,
                "exit_action": saved_exit_action,
                "premium_gate": _gate_text(enabled, new_gate["threshold"]),
            },
            overrides={"premium_gate": new_gate},
            is_saved=False,
        ))
    return out


def settings_of(saved: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    """A stored row's whole settings: the run's saved ones with the row's overrides."""
    return {**saved, **(row.get("overrides") or {})}


def comparison_row(
    combo: Combo,
    summary: dict[str, Any],
    trades: list[dict[str, Any]],
    *,
    complete: bool,
    stopped_at: Optional[str] = None,
) -> dict[str, Any]:
    """One line of the comparison table. `trades` are the combination's campaigns
    (`backtest_job.card_trades`); `summary` is its replay's."""
    finished = [t for t in trades if t.get("exit_reason") != "open_at_period_end"]
    wins = sum(1 for t in finished if float(t.get("net_pnl") or 0) > 0)
    worst = [float(t["worst_pnl_at_check"]) for t in trades if t.get("worst_pnl_at_check") is not None]
    return {
        "id": combo.id,
        "label": combo.label,
        "varied": combo.varied,
        "overrides": combo.overrides,
        "exit_action": combo.exit_action,
        "is_saved": combo.is_saved,
        "trades": len(trades),
        "rolls": int(summary.get("rolls") or 0),
        "win_rate_pct": round(100.0 * wins / len(finished), 1) if finished else None,
        "net_pnl": round(sum(float(t.get("net_pnl") or 0) for t in trades), 2),
        "friction": round(sum(float(t.get("friction") or 0) for t in trades), 2),
        # Drawdown of the equity at every check, not of campaign closes: a condor's worst
        # moment is usually mid-campaign.
        "max_drawdown": -float(summary.get("max_drawdown") or 0.0),
        "worst_at_check": min(worst, default=None),
        "complete": complete,
        "status": "completed" if complete else "partial",
        "stopped_at": stopped_at,
        # What the bot's evidence (#66) shows for these settings.
        "closed_pnl": summary.get("closed_pnl"),
        "open_campaign_cash": summary.get("open_campaign_cash"),
    }


def headline(period_text: str, rows: list[dict[str, Any]], partial: bool) -> str:
    saved = next((r for r in rows if r["is_saved"]), rows[0] if rows else None)
    if saved is None:
        return f"{period_text}: nothing replayed."
    text = (f"{period_text}: your settings made {saved['trades']} campaign(s), "
            f"net ₹{float(saved['net_pnl'] or 0):,.0f}")
    if saved["max_drawdown"]:
        text += f", max drawdown ₹{abs(saved['max_drawdown']):,.0f}"
    text += "."
    if len(rows) > 1:
        best = max(rows, key=lambda r: float(r["net_pnl"] or 0))
        if best is not saved:
            text += f" Best of {len(rows)} combinations: {best['label']}, net ₹{float(best['net_pnl'] or 0):,.0f}."
        else:
            text += f" They were the best of {len(rows)} combinations compared."
    if partial:
        unfinished = sum(1 for r in rows if not r["complete"])
        text += (f" {unfinished} of {len(rows)} combination(s) replayed in part: run it again to continue."
                 if unfinished else " Replayed in part: see the notes.")
    return text
