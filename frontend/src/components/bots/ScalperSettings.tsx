"use client";

import { useContext, useEffect } from "react";

import { Checkbox } from "@/components/ui/Checkbox";
import { FieldValidityContext, NumberInput } from "@/components/ui/NumberInput";
import {
  MARKET_CLOSE,
  MAX_SESSION_WINDOWS,
  suggestWindow,
  validateSessions,
  warmupWarning,
} from "@/lib/scalper-sessions";
import {
  BOT_IRON_FLY_SCALPER,
  type Bot,
  type IronFlyScalperConfig,
  type MomentumLongScalperConfig,
  type SessionWindow,
} from "@/lib/use-bots";

export type Tab = { id: string; label: string };

// "schedule" first, and the same id the other two bots already use for their timing tab.
// When a bot trades is the first thing a user wants to change and the last thing they want
// to hunt for.
export const MOMENTUM_TABS: Tab[] = [
  { id: "schedule", label: "Schedule" },
  { id: "signal", label: "Signal" },
  { id: "exits", label: "Exits" },
  { id: "risk", label: "Risk" },
];

export const IRON_FLY_TABS: Tab[] = [
  { id: "schedule", label: "Schedule" },
  { id: "structure", label: "Structure" },
  { id: "exits", label: "Exits" },
  { id: "reentry", label: "Re-entry" },
  { id: "risk", label: "Risk" },
];

function Num({
  label,
  hint,
  value,
  onChange,
  disabled,
  min = 0,
  max = 1_000_000,
  step = 1,
  suffix,
}: {
  label: string;
  hint?: string;
  value: number;
  onChange: (next: number) => void;
  disabled: boolean;
  min?: number;
  max?: number;
  step?: number;
  suffix?: string;
}) {
  return (
    <label className="block">
      <span className="block text-micro font-semibold uppercase tracking-[0.06em] text-faint">
        {label}
      </span>
      <div className="mt-1 flex items-center gap-2">
        <NumberInput
          min={min}
          max={max}
          step={step}
          value={value}
          disabled={disabled}
          aria-label={label}
          onChange={(v) => onChange(v)}
          className="app-input w-32 font-mono tabular-nums"
        />
        {suffix && <span className="text-hint text-faint">{suffix}</span>}
      </div>
      {hint && <span className="mt-1 block text-hint text-faint">{hint}</span>}
    </label>
  );
}

function Check({
  label,
  hint,
  checked,
  onChange,
  disabled,
}: {
  label: string;
  hint?: string;
  checked: boolean;
  onChange: (next: boolean) => void;
  disabled: boolean;
}) {
  return (
    <label className="flex items-start gap-2">
      <Checkbox
        checked={checked}
        disabled={disabled}
        onChange={onChange}
        className="mt-0.5"
      />
      <span>
        <span className="block text-body text-text">{label}</span>
        {hint && <span className="mt-0.5 block text-hint text-faint">{hint}</span>}
      </span>
    </label>
  );
}

function Time({
  label,
  value,
  onChange,
  disabled,
  min,
  max,
}: {
  label: string;
  value: string;
  onChange: (next: string) => void;
  disabled: boolean;
  min?: string;
  max?: string;
}) {
  return (
    <label className="block">
      <span className="block text-micro font-semibold uppercase tracking-[0.06em] text-faint">
        {label}
      </span>
      <input
        type="time"
        className="app-input mt-1 w-32 font-mono tabular-nums"
        aria-label={label}
        value={value}
        min={min}
        max={max}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
      />
    </label>
  );
}

/** When the bot is allowed to open a position, and when it must be flat.
 *
 *  Both bots evaluate continuously inside their windows, so "start and stop" is a list of
 *  windows rather than one pair. The square-off sits alongside them because it is the rule
 *  the windows have to satisfy -- editing one without the other is how you get a window the
 *  backend refuses.
 */
function ScheduleTab({
  config,
  onConfig,
  disabled,
  isFly,
}: {
  config: MomentumLongScalperConfig | IronFlyScalperConfig;
  onConfig: (patch: Record<string, unknown>) => void;
  disabled: boolean;
  isFly: boolean;
}) {
  const sessions = config.sessions ?? [];
  const squareOff = config.hard_square_off_ist;
  const error = validateSessions(sessions, squareOff);
  // The iron fly has no signal block of its own -- the runtime falls back to the default
  // 9/20 periods for it -- so its warm-up is always the 20 minutes the floor already covers.
  const warning = warmupWarning(
    sessions,
    isFly ? null : (config as MomentumLongScalperConfig).signal,
  );

  // Save is disabled while this is red, through the same context the number fields use.
  const reportValidity = useContext(FieldValidityContext);
  useEffect(() => {
    reportValidity?.("scalper_sessions", error === null);
    return () => reportValidity?.("scalper_sessions", true);
  }, [reportValidity, error]);

  function patchWindow(index: number, patch: Partial<SessionWindow>) {
    onConfig({
      sessions: sessions.map((w, i) => (i === index ? { ...w, ...patch } : w)),
    });
  }

  const next = suggestWindow(sessions, squareOff);
  const canAdd = sessions.length < MAX_SESSION_WINDOWS && next !== null;

  return (
    <div className="grid gap-4">
      <div className="grid gap-3">
        {sessions.map((w, i) => (
          <div key={i} className="flex items-end gap-3">
            <Time
              label={i === 0 ? "Trades from" : "…and from"}
              value={w.start}
              disabled={disabled}
              max={w.end}
              onChange={(start) => patchWindow(i, { start })}
            />
            <Time
              label="Until"
              value={w.end}
              disabled={disabled}
              min={w.start}
              max={squareOff < MARKET_CLOSE ? squareOff : MARKET_CLOSE}
              onChange={(end) => patchWindow(i, { end })}
            />
            <button
              type="button"
              className="app-btn-outline mb-1 text-hint"
              // One window is the minimum: a bot with none is armed with nowhere to trade.
              disabled={disabled || sessions.length <= 1}
              onClick={() => onConfig({ sessions: sessions.filter((_, j) => j !== i) })}
            >
              Remove
            </button>
          </div>
        ))}
      </div>

      <div>
        <button
          type="button"
          className="app-btn-secondary text-hint"
          disabled={disabled || !canAdd}
          // Sorted on insert, not on every keystroke: a suggestion can land in a gap
          // between two existing windows, and rows that reorder while you type are worse.
          onClick={() =>
            next &&
            onConfig({
              sessions: [...sessions, next].sort((a, b) => a.start.localeCompare(b.start)),
            })
          }
        >
          Add window
        </button>
        <span className="ml-2 text-hint text-faint">
          {sessions.length} of {MAX_SESSION_WINDOWS}
          {!canAdd && sessions.length < MAX_SESSION_WINDOWS
            ? " — no room left before the square-off"
            : ""}
        </span>
      </div>

      <Time
        label="Flat by (square-off)"
        value={squareOff}
        disabled={disabled}
        max={MARKET_CLOSE}
        onChange={(hard_square_off_ist) => onConfig({ hard_square_off_ist })}
      />

      {error && (
        <p role="alert" className="text-hint text-down">
          {error}
        </p>
      )}
      {!error && warning && <p className="text-hint text-faint">{warning}</p>}

      <p className="app-card-muted p-3 text-hint">
        {isFly
          ? "The bot flattens when a window closes, and squares off everything at the time above whatever happens."
          : "A position opened inside a window is allowed to run past it — the ladder decides when to leave. The square-off above is the hard backstop."}
      </p>
    </div>
  );
}

export function ScalperSettings({
  bot,
  tab,
  config,
  onConfig,
  disabled,
}: {
  bot: Bot;
  tab: string;
  config: MomentumLongScalperConfig | IronFlyScalperConfig;
  onConfig: (patch: Record<string, unknown>) => void;
  disabled: boolean;
}) {
  const isFly = bot.bot_type === BOT_IRON_FLY_SCALPER;

  if (tab === "schedule") {
    return (
      <ScheduleTab config={config} onConfig={onConfig} disabled={disabled} isFly={isFly} />
    );
  }

  if (tab === "risk") {
    const risk = config.risk;
    return (
      <div className="grid gap-4 sm:grid-cols-2">
        <Num label="Daily loss cap" suffix="₹" min={100} max={10_000_000} step={500}
          hint="Realized plus the open position. Hitting it closes the position and disables the bot until you re-enable it."
          value={risk.cumulative_stop_inr}
          onChange={(v) => onConfig({ risk: { ...risk, cumulative_stop_inr: v } })} disabled={disabled} />
        <Num label="Consecutive losses" min={1} max={20}
          hint="Losing cycles in a row before the bot pauses."
          value={risk.consecutive_loss_limit}
          onChange={(v) => onConfig({ risk: { ...risk, consecutive_loss_limit: v } })} disabled={disabled} />
        <Num label="Cooldown" suffix="min" min={1} max={240}
          value={risk.cooldown_minutes}
          onChange={(v) => onConfig({ risk: { ...risk, cooldown_minutes: v } })} disabled={disabled} />
        <Num label="Broker calls held back" min={0} max={90}
          hint="Reserved so scalping can never starve the dashboard, the other bots, or a manual square-off."
          value={risk.api_budget_reserve_calls}
          onChange={(v) => onConfig({ risk: { ...risk, api_budget_reserve_calls: v } })} disabled={disabled} />
        <div className="sm:col-span-2">
          {/* Charges are deployment-wide and shared with every other bot and the backtest,
              so they are edited in one place rather than per bot. A pointer, not a copy:
              two editors for one record is how they drift. */}
          <p className="app-card-muted mb-4 p-3 text-hint">
            Brokerage and taxes are set once for all bots in{" "}
            <a className="app-link" href="/settings?tab=trading-costs">
              Settings › Trading Costs
            </a>
            . The daily loss cap above is measured net of them.
          </p>
          <Check label="Trade on expiry day"
            hint="Off by default. On expiry day a Strategy Group rule may already be armed on this expiry — see the warning the bot sends if it finds one."
            checked={config.trade_on_expiry_day}
            onChange={(v) => onConfig({ trade_on_expiry_day: v })} disabled={disabled} />
        </div>
      </div>
    );
  }

  if (!isFly) {
    const cfg = config as MomentumLongScalperConfig;
    if (tab === "signal") {
      return (
        <div className="grid gap-4 sm:grid-cols-2">
          <Num label="Premium outlay" suffix="₹" min={1000} max={10_000_000} step={1000}
            hint="Capital deployed, not risked. Lots = outlay ÷ cost, and an ATM option cheapens towards expiry — so this buys more lots the nearer expiry gets."
            value={cfg.premium_outlay_inr}
            onChange={(v) => onConfig({ premium_outlay_inr: v })} disabled={disabled} />
          <Num label="EMA period" min={2} max={200}
            value={cfg.signal.ema_period}
            onChange={(v) => onConfig({ signal: { ...cfg.signal, ema_period: v } })} disabled={disabled} />
          <Num label="Volume MA period" min={2} max={200}
            hint="Also sets warm-up: the bot cannot signal until this many 1-minute bars exist."
            value={cfg.signal.volume_ma_period}
            onChange={(v) => onConfig({ signal: { ...cfg.signal, volume_ma_period: v } })} disabled={disabled} />
          <Num label="Volume multiplier" suffix="×" min={0.1} max={10} step={0.1}
            hint="A bar must exceed the average by this much to count as a surge."
            value={cfg.signal.volume_multiplier}
            onChange={(v) => onConfig({ signal: { ...cfg.signal, volume_multiplier: v } })} disabled={disabled} />
          <div className="sm:col-span-2">
            <Check label="Require price on the right side of VWAP"
              hint="Read from the futures feed. Without volume there is no VWAP, which is why the signal runs on futures rather than the index."
              checked={cfg.signal.require_vwap}
              onChange={(v) => onConfig({ signal: { ...cfg.signal, require_vwap: v } })} disabled={disabled} />
          </div>
        </div>
      );
    }
    const e = cfg.exits;
    return (
      <div className="grid gap-4 sm:grid-cols-2">
        <Num label="Runner trigger" suffix="pts" min={0.5} max={500} step={0.5}
          hint="Not a take-profit: reaching it starts the trailing runner rather than closing the trade."
          value={e.target_pts} onChange={(v) => onConfig({ exits: { ...e, target_pts: v } })} disabled={disabled} />
        <Num label="Initial stop" suffix="pts" min={0.5} max={500} step={0.5}
          value={e.stop_loss_pts} onChange={(v) => onConfig({ exits: { ...e, stop_loss_pts: v } })} disabled={disabled} />
        <Num label="Time stop" suffix="sec" min={5} max={3600} step={5}
          value={e.time_invalidation_seconds}
          onChange={(v) => onConfig({ exits: { ...e, time_invalidation_seconds: v } })} disabled={disabled} />
        <Num label="…unless it has gained" suffix="pts" min={0} max={500} step={0.5}
          hint="Measured against the best gain reached, not the current one: a trade that ran up and came back has moved, and belongs to the stop rather than the clock."
          value={e.time_invalidation_min_move_pts}
          onChange={(v) => onConfig({ exits: { ...e, time_invalidation_min_move_pts: v } })} disabled={disabled} />
        <Num label="Level 1 trigger" suffix="pts" min={0.5} max={500} step={0.5}
          value={e.level_1_trigger_pts}
          onChange={(v) => onConfig({ exits: { ...e, level_1_trigger_pts: v } })} disabled={disabled} />
        <Num label="Level 1 locks" suffix="pts" min={0} max={500} step={0.1}
          value={e.level_1_lock_pts}
          onChange={(v) => onConfig({ exits: { ...e, level_1_lock_pts: v } })} disabled={disabled} />
        <Num label="Level 2 trigger" suffix="pts" min={0.5} max={500} step={0.5}
          value={e.level_2_trigger_pts}
          onChange={(v) => onConfig({ exits: { ...e, level_2_trigger_pts: v } })} disabled={disabled} />
        <Num label="Level 2 locks" suffix="pts" min={0} max={500} step={0.5}
          value={e.level_2_lock_pts}
          onChange={(v) => onConfig({ exits: { ...e, level_2_lock_pts: v } })} disabled={disabled} />
        <Num label="Runner trails by" suffix="pts" min={0.5} max={500} step={0.5}
          hint="Behind the highest price reached, so a pullback tightens nothing."
          value={e.level_3_runner_step_pts}
          onChange={(v) => onConfig({ exits: { ...e, level_3_runner_step_pts: v } })} disabled={disabled} />
      </div>
    );
  }

  const fly = config as IronFlyScalperConfig;
  if (tab === "structure") {
    const s = fly.structure;
    return (
      <div className="grid gap-4 sm:grid-cols-2">
        <Num label="Wing width" suffix="pts" min={25} max={2000} step={25}
          hint="Narrower collects less credit AND needs more margin, so wings always snap outward from the target, never inward."
          value={s.wing_width_points}
          onChange={(v) => onConfig({ structure: { ...s, wing_width_points: v } })} disabled={disabled} />
        <Num label="Margin ceiling" suffix="₹" min={1000} max={100_000_000} step={5000}
          hint="The bot takes the largest whole-lot fly that fits, verified through one margin call carrying all four legs. If one lot will not fit it skips rather than partially funding."
          value={fly.margin_ceiling_inr}
          onChange={(v) => onConfig({ margin_ceiling_inr: v })} disabled={disabled} />
        <Num label="Widen above VIX" min={0} max={100} step={0.5}
          hint="Zero switches the rule off. VIX is only fetched when this is on."
          value={s.widen_above_vix ?? 0}
          onChange={(v) => onConfig({ structure: { ...s, widen_above_vix: v > 0 ? v : null } })}
          disabled={disabled} />
        <Num label="Widened wing width" suffix="pts" min={25} max={2000} step={25}
          value={s.widened_wing_width_points}
          onChange={(v) => onConfig({ structure: { ...s, widened_wing_width_points: v } })} disabled={disabled} />
      </div>
    );
  }
  if (tab === "reentry") {
    const r = fly.reentry;
    return (
      <div className="space-y-4">
        <p className="app-text-muted text-hint">
          Both conditions must clear before another fly. The cooldown alone re-centres into an
          ongoing move and gets stopped again; the range test alone re-fires in a chop that
          keeps clearing the band.
        </p>
        <div className="grid gap-4 sm:grid-cols-2">
          <Num label="Cooldown" suffix="min" min={0} max={240}
            value={r.cooldown_minutes}
            onChange={(v) => onConfig({ reentry: { ...r, cooldown_minutes: v } })} disabled={disabled} />
          <Num label="Range window" suffix="min" min={1} max={120}
            value={r.range_window_minutes}
            onChange={(v) => onConfig({ reentry: { ...r, range_window_minutes: v } })} disabled={disabled} />
          <Num label="Spot must stay within" suffix="%" min={0.01} max={10} step={0.01}
            value={r.max_range_pct}
            onChange={(v) => onConfig({ reentry: { ...r, max_range_pct: v } })} disabled={disabled} />
        </div>
      </div>
    );
  }

  const e = fly.exits;
  return (
    <div className="space-y-4">
      <p className="app-text-muted text-hint">
        Both loss stops are live and <b>the tighter one binds</b>. Set either to zero to switch
        it off. A flat rupee stop does not scale with lot count — at three lots ₹1,500 is ₹500
        a lot, which ordinary movement clears — while a share of credit scales with size,
        expiry and volatility.
      </p>
      <div className="grid gap-4 sm:grid-cols-2">
        <Num label="Book at credit decay of" suffix="%" min={1} max={100} step={1}
          hint="Measured at what unwinding would actually cost — shorts bought back at the ask, wings sold at the bid."
          value={e.target_decay_pct}
          onChange={(v) => onConfig({ exits: { ...e, target_decay_pct: v } })} disabled={disabled} />
        <Num label="Spot drift stop" suffix="%" min={0.01} max={10} step={0.01}
          hint="Checked before the P&L stops: once the centre has moved this far, gamma arrives faster than an exit can be placed."
          value={e.max_spot_drift_pct}
          onChange={(v) => onConfig({ exits: { ...e, max_spot_drift_pct: v } })} disabled={disabled} />
        <Num label="Hard stop" suffix="₹" min={0} max={10_000_000} step={100}
          hint="Absolute, per position. Zero switches it off."
          value={e.hard_stop_loss_inr ?? 0}
          onChange={(v) => onConfig({ exits: { ...e, hard_stop_loss_inr: v > 0 ? v : null } })}
          disabled={disabled} />
        <Num label="Stop at credit loss of" suffix="%" min={0} max={500} step={1}
          hint="Share of the credit collected. Zero switches it off."
          value={e.stop_loss_credit_pct ?? 0}
          onChange={(v) => onConfig({ exits: { ...e, stop_loss_credit_pct: v > 0 ? v : null } })}
          disabled={disabled} />
      </div>
    </div>
  );
}
