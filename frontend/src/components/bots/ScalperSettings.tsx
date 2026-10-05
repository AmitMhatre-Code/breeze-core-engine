"use client";

import { useContext, useEffect } from "react";

import {
  SettingsError,
  TabIntro,
  WorkedExample,
  num,
  rupees,
} from "@/components/bots/SettingsHelp";
import { SignalChoicePicker } from "@/components/bots/SignalChoicePicker";
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
  { id: "filter", label: "Entry filter" },
  { id: "risk", label: "Risk" },
];

function Select({
  label,
  hint,
  value,
  onChange,
  disabled,
  options,
}: {
  label: string;
  hint?: string;
  value: string;
  onChange: (next: string) => void;
  disabled: boolean;
  options: { value: string; label: string }[];
}) {
  return (
    <label className="block">
      <span className="block text-micro font-semibold uppercase tracking-[0.06em] text-faint">{label}</span>
      <select
        className="app-input mt-1 w-full max-w-xl"
        aria-label={label}
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
      >
        {/* A stored value no longer listed still shows, so it is visible, not silently swapped. */}
        {options.some((o) => o.value === value) ? null : <option value={value}>{value} (not found)</option>}
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
      {hint && <span className="mt-1 block text-hint text-faint">{hint}</span>}
    </label>
  );
}

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
      <TabIntro>
        <p>
          {isFly
            ? "The windows (IST) when a fly may be open. The bot opens a fly only inside a window and closes it when the window ends."
            : "The windows (IST) when the bot may open a trade. Inside a window it watches the signal continuously and trades each fresh call."}{" "}
          Up to {MAX_SESSION_WINDOWS} windows, all before the square-off.
        </p>
      </TabIntro>
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
        <div className="sm:col-span-2">
          <TabIntro>
            <p>
              Circuit breakers that stop a bad day getting worse, on top of the stops on the Exits tab. They can
              only stop the bot trading or close its position; they never open anything.
            </p>
          </TabIntro>
        </div>
        <Num label="Daily loss cap" suffix="₹" min={100} max={10_000_000} step={500}
          hint="Today's closed trades plus the open one, after charges. Hitting it closes the position and switches the bot off until you switch it back on."
          value={risk.cumulative_stop_inr}
          onChange={(v) => onConfig({ risk: { ...risk, cumulative_stop_inr: v } })} disabled={disabled} />
        <Num label="Consecutive losses" min={1} max={20}
          hint="Losing trades in a row that make the bot pause."
          value={risk.consecutive_loss_limit}
          onChange={(v) => onConfig({ risk: { ...risk, consecutive_loss_limit: v } })} disabled={disabled} />
        <Num label="Cooldown" suffix="min" min={1} max={240}
          hint="How long that pause lasts, counted from the last losing trade's close."
          value={risk.cooldown_minutes}
          onChange={(v) => onConfig({ risk: { ...risk, cooldown_minutes: v } })} disabled={disabled} />
        <Num label="Broker calls held back" min={0} max={90}
          hint="ICICI allows a limited number of calls a minute, shared by the whole app. The bot opens nothing while fewer than this many are left, so it can never starve the dashboard, the other bots or a manual square-off."
          value={risk.api_budget_reserve_calls}
          onChange={(v) => onConfig({ risk: { ...risk, api_budget_reserve_calls: v } })} disabled={disabled} />
        <div className="sm:col-span-2">
          <WorkedExample title="With these settings">
            <li>
              Once today&rsquo;s losses reach <b>{rupees(risk.cumulative_stop_inr, 0)}</b>, the bot closes what it holds
              and switches itself off.
            </li>
            <li>
              After <b>{risk.consecutive_loss_limit}</b> losing trade{risk.consecutive_loss_limit === 1 ? "" : "s"} in a
              row, it opens nothing for <b>{risk.cooldown_minutes} minutes</b>.
            </li>
          </WorkedExample>
        </div>
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
            hint="Off by default. On expiry day a Profit Booking / Stop Loss rule may already be armed on this expiry — the bot warns you if it finds one."
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
          <div className="sm:col-span-2 space-y-2">
            <TabIntro>
              <p>
                What makes the bot trade. A signal&rsquo;s bullish or bearish reading is called a <b>call</b> (not to
                be confused with a call option). On each fresh call the bot buys one at-the-money NIFTY option on the
                nearest weekly expiry.
              </p>
            </TabIntro>
            <SignalChoicePicker
              value={cfg.signal}
              disabled={disabled}
              onChange={(signal) => onConfig({ signal })}
              directionHint="Buys the at-the-money call option when the signal is bullish and the put when it is bearish — the other way round when fading."
            />
            <p className="app-card-muted p-3 text-hint">
              One trade per call: after trading a call the bot waits for the signal to go quiet and fire afresh. The
              call going quiet does not close the trade — the stops on the Exits tab, a call the other way or the
              square-off do. Compare every signal for this bot with its backtest.
            </p>
          </div>
          <Num label="Premium outlay" suffix="₹" min={1000} max={10_000_000} step={1000}
            hint="What each trade spends on the option — the capital deployed, not the amount at risk (the stop on the Exits tab sets that). Lots = outlay ÷ the cost of one lot, so as an ATM option cheapens towards expiry the same outlay buys more lots."
            value={cfg.premium_outlay_inr}
            onChange={(v) => onConfig({ premium_outlay_inr: v })} disabled={disabled} />
        </div>
      );
    }
    return <LadderExits exits={cfg.exits} onConfig={onConfig} disabled={disabled} />;
  }

  const fly = config as IronFlyScalperConfig;
  if (tab === "structure") {
    const s = fly.structure;
    const vixOn = (s.widen_above_vix ?? 0) > 0;
    return (
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="sm:col-span-2">
          <TabIntro>
            <p>
              An iron fly sells the at-the-money NIFTY call and put on the nearest weekly expiry, collecting a
              credit, and buys a call above and a put below as <b>wings</b>. It earns when NIFTY stays near the
              centre; the wings cap what it can lose if NIFTY runs.
            </p>
          </TabIntro>
        </div>
        <Num label="Wing width" suffix="pts" min={25} max={2000} step={25}
          hint="How far each bought wing sits from the centre strike, in index points. Wider wings keep more of the credit but allow a bigger worst case. Rounded outward to a listed strike, never inward."
          value={s.wing_width_points}
          onChange={(v) => onConfig({ structure: { ...s, wing_width_points: v } })} disabled={disabled} />
        <Num label="Margin ceiling" suffix="₹" min={1000} max={100_000_000} step={5000}
          hint="The most margin one fly may use. The bot takes the largest whole number of lots that ICICI confirms fits, checked on all four legs together, and skips if even one lot will not. Size it to your daily loss cap: ₹25,000 is about three lots."
          value={fly.margin_ceiling_inr}
          onChange={(v) => onConfig({ margin_ceiling_inr: v })} disabled={disabled} />
        <Num label="Widen above VIX" min={0} max={100} step={0.5}
          hint="When India VIX (expected volatility) is above this as a fly opens, the wider wings below are used instead. Zero switches the rule off, and VIX is only fetched while it is on."
          value={s.widen_above_vix ?? 0}
          onChange={(v) => onConfig({ structure: { ...s, widen_above_vix: v > 0 ? v : null } })}
          disabled={disabled} />
        <Num label="Widened wing width" suffix="pts" min={25} max={2000} step={25}
          hint="The wing width used on a high-VIX day."
          value={s.widened_wing_width_points}
          onChange={(v) => onConfig({ structure: { ...s, widened_wing_width_points: v } })} disabled={disabled} />
        <div className="sm:col-span-2">
          <WorkedExample title="With these settings">
            <li>
              Sells the at-the-money call and put, and buys a call <b>{num(s.wing_width_points)} pts</b> above and a put{" "}
              <b>{num(s.wing_width_points)} pts</b> below.
            </li>
            <li>
              The most it can lose is the wing width minus the credit collected: {num(s.wing_width_points)} pts minus
              the credit, per unit, before charges.
            </li>
            <li>
              {vixOn ? (
                <>
                  With VIX above {num(s.widen_above_vix ?? 0)}, the wings move out to{" "}
                  <b>{num(s.widened_wing_width_points)} pts</b>.
                </>
              ) : (
                "The wings never widen: the VIX rule is off."
              )}
            </li>
          </WorkedExample>
        </div>
      </div>
    );
  }
  if (tab === "filter") {
    return <FlyEntryFilter fly={fly} onConfig={onConfig} disabled={disabled} />;
  }
  if (tab === "reentry") {
    const r = fly.reentry;
    return (
      <div className="space-y-4">
        <TabIntro>
          <p>
            When a fly may open again after one closes. <b>Both</b> conditions must clear. The cooldown alone would
            re-open straight into a move that is still running and get stopped again; the range test alone would
            re-open in a choppy market that keeps swinging back through the band.
          </p>
        </TabIntro>
        <div className="grid gap-4 sm:grid-cols-2">
          <Num label="Cooldown" suffix="min" min={0} max={240}
            hint="Minutes to wait after a fly closes before another may open. Zero means no wait."
            value={r.cooldown_minutes}
            onChange={(v) => onConfig({ reentry: { ...r, cooldown_minutes: v } })} disabled={disabled} />
          <div className="hidden sm:block" aria-hidden />
          <Num label="Range window" suffix="min" min={1} max={120}
            hint="The recent stretch of NIFTY that must have been calm…"
            value={r.range_window_minutes}
            onChange={(v) => onConfig({ reentry: { ...r, range_window_minutes: v } })} disabled={disabled} />
          <Num label="Spot must stay within" suffix="%" min={0.01} max={10} step={0.01}
            hint="…meaning its high and low over that stretch are within this much of each other."
            value={r.max_range_pct}
            onChange={(v) => onConfig({ reentry: { ...r, max_range_pct: v } })} disabled={disabled} />
        </div>
        <WorkedExample title="With these settings">
          <li>
            After a fly closes, the bot waits at least <b>{r.cooldown_minutes} minutes</b>.
          </li>
          <li>
            Then it opens the next fly only once NIFTY&rsquo;s high and low over the last{" "}
            <b>{r.range_window_minutes} minutes</b> are within <b>{num(r.max_range_pct)}%</b> of each other — about{" "}
            {num((EXAMPLE_INDEX * r.max_range_pct) / 100, 0)} points with NIFTY at {num(EXAMPLE_INDEX, 0)}.
          </li>
        </WorkedExample>
      </div>
    );
  }

  const e = fly.exits;
  const flyTarget = (EXAMPLE_CREDIT * e.target_decay_pct) / 100;
  const flyStops = [
    e.stop_loss_credit_pct != null ? (EXAMPLE_CREDIT * e.stop_loss_credit_pct) / 100 : null,
    e.hard_stop_loss_inr ?? null,
  ].filter((v): v is number => v != null);
  const flyStop = flyStops.length ? Math.min(...flyStops) : null;
  return (
    <div className="space-y-4">
      <TabIntro>
        <p>
          A fly closes on whichever of these comes first, or when its window ends. The <b>credit</b> is what the
          fly collected when it opened. Profit and loss are measured at what closing would really cost: the sold
          options bought back at the ask, the wings sold at the bid.
        </p>
        <p>
          The two loss stops are both live and <b>the tighter one wins</b>. Set either to zero to switch it off. A
          share of the credit grows with the number of lots; a flat rupee stop does not, so on many lots it can fire
          on ordinary noise — which is why the hard stop ships off.
        </p>
      </TabIntro>
      <div className="grid gap-4 sm:grid-cols-2">
        <Num label="Book at credit decay of" suffix="%" min={1} max={100} step={1}
          hint="Takes profit once this share of the credit has been earned."
          value={e.target_decay_pct}
          onChange={(v) => onConfig({ exits: { ...e, target_decay_pct: v } })} disabled={disabled} />
        <Num label="Spot drift stop" suffix="%" min={0.01} max={10} step={0.01}
          hint="Closes once NIFTY has moved this far from the centre strike, whatever the P&L. Checked first: past this point, losses on the side NIFTY is moving towards grow faster than an exit can be placed."
          value={e.max_spot_drift_pct}
          onChange={(v) => onConfig({ exits: { ...e, max_spot_drift_pct: v } })} disabled={disabled} />
        <Num label="Hard stop" suffix="₹" min={0} max={10_000_000} step={100}
          hint="Closes once the fly is down this many rupees, whatever its size. Zero switches it off."
          value={e.hard_stop_loss_inr ?? 0}
          onChange={(v) => onConfig({ exits: { ...e, hard_stop_loss_inr: v > 0 ? v : null } })}
          disabled={disabled} />
        <Num label="Stop at credit loss of" suffix="%" min={0} max={500} step={1}
          hint="Closes once the loss reaches this share of the credit collected. Zero switches it off."
          value={e.stop_loss_credit_pct ?? 0}
          onChange={(v) => onConfig({ exits: { ...e, stop_loss_credit_pct: v > 0 ? v : null } })}
          disabled={disabled} />
      </div>
      <WorkedExample title={<>Example with these settings: a fly that collected {rupees(EXAMPLE_CREDIT, 0)}</>}>
        <li>
          Books its profit once it is <b>{rupees(flyTarget, 0)}</b> up.
        </li>
        <li>
          {flyStop == null ? (
            "Has no loss stop at all: both are off, so only the drift stop, the wings and the window's end limit it."
          ) : (
            <>
              Stops out once it is <b>{rupees(flyStop, 0)}</b> down
              {flyStops.length > 1 ? " — the tighter of the two loss stops" : ""}.
            </>
          )}
        </li>
        <li>
          Closes regardless if NIFTY moves <b>{num(e.max_spot_drift_pct)}%</b> from the centre — about{" "}
          {num((EXAMPLE_INDEX * e.max_spot_drift_pct) / 100, 0)} points with NIFTY at {num(EXAMPLE_INDEX, 0)}.
        </li>
      </WorkedExample>
    </div>
  );
}

// Illustrative figures for the Iron Fly's worked examples. Points-from-a-percentage needs an
// index level, and a rupee example needs a credit; round numbers keep the arithmetic visible
// rather than pretending to be today's market.
const EXAMPLE_INDEX = 25_000;
const EXAMPLE_CREDIT = 10_000;

type LadderConfig = MomentumLongScalperConfig["exits"];

/** The same refusals as `TrailingLadderConfig._ladder_is_monotonic`, so a ladder the backend
 *  would reject says why here instead of failing on Save. */
function ladderError(e: LadderConfig): string | null {
  if (e.level_2_trigger_pts <= e.level_1_trigger_pts) return "Level 2 must trigger above level 1.";
  if (e.target_pts <= e.level_2_trigger_pts) return "The runner trigger must sit above level 2's trigger.";
  if (e.level_2_lock_pts <= e.level_1_lock_pts) return "Level 2 must lock in more than level 1.";
  if (e.level_1_lock_pts >= e.level_1_trigger_pts) return "Level 1 cannot lock in more than it has gained.";
  if (e.level_2_lock_pts >= e.level_2_trigger_pts) return "Level 2 cannot lock in more than it has gained.";
  return null;
}

// A round entry price for the worked example: the ladder is in points from entry, so the
// example reads the same whatever the option actually costs.
const EXAMPLE_ENTRY = 100;

/** The Long Scalper's trailing ladder (`scalping/ladder.py`), laid out in the order the stop
 *  climbs it, with a worked example on the user's own numbers. Field names alone ("Level 1
 *  locks") mean nothing until you can see a price walk up the rungs. */
function LadderExits({
  exits: e,
  onConfig,
  disabled,
}: {
  exits: LadderConfig;
  onConfig: (patch: Record<string, unknown>) => void;
  disabled: boolean;
}) {
  const error = ladderError(e);

  const reportValidity = useContext(FieldValidityContext);
  useEffect(() => {
    reportValidity?.("scalper_ladder", error === null);
    return () => reportValidity?.("scalper_ladder", true);
  }, [reportValidity, error]);

  const set = (patch: Partial<LadderConfig>) => onConfig({ exits: { ...e, ...patch } });

  // Any high past the runner trigger shows the trail; the lock-in floor still applies under it.
  const exampleHigh = EXAMPLE_ENTRY + e.target_pts + 5;
  const exampleTrail = Math.max(EXAMPLE_ENTRY + e.level_2_lock_pts, exampleHigh - e.level_3_runner_step_pts);

  return (
    <div className="space-y-5">
      <TabIntro>
        <p>
          Every trade starts with a stop below the price paid. As the option gains, the stop climbs a ladder of
          levels and <b>never moves back down</b>, turning a winner into locked-in profit.
        </p>
        <p>
          All values are in <b>option points</b>{" "}— rupees per unit of the option&rsquo;s premium, not index points —
          measured from the entry price and checked against the <b>bid</b>, the price the bot could actually sell at.
          Besides these stops, a trade closes if the signal calls the other way, and at the square-off time.
        </p>
      </TabIntro>

      <div className="grid gap-4 sm:grid-cols-2">
        <Num label="Initial stop" suffix="pts" min={0.5} max={500} step={0.5}
          hint="Sells if the option falls this far below the price paid. The most a trade can lose per unit, before charges."
          value={e.stop_loss_pts} onChange={(v) => set({ stop_loss_pts: v })} disabled={disabled} />
        <div className="hidden sm:block" aria-hidden />

        <Num label="Level 1 trigger" suffix="pts" min={0.5} max={500} step={0.5}
          hint="Once the option is this far above entry…"
          value={e.level_1_trigger_pts} onChange={(v) => set({ level_1_trigger_pts: v })} disabled={disabled} />
        <Num label="Level 1 locks" suffix="pts" min={0} max={500} step={0.1}
          hint="…the stop moves up to entry plus this much, so a reversal now exits around break-even instead of at a loss. A little above zero covers charges."
          value={e.level_1_lock_pts} onChange={(v) => set({ level_1_lock_pts: v })} disabled={disabled} />

        <Num label="Level 2 trigger" suffix="pts" min={0.5} max={500} step={0.5}
          hint="Once the option is this far above entry…"
          value={e.level_2_trigger_pts} onChange={(v) => set({ level_2_trigger_pts: v })} disabled={disabled} />
        <Num label="Level 2 locks" suffix="pts" min={0} max={500} step={0.5}
          hint="…the stop moves up to entry plus this much, locking in a profit."
          value={e.level_2_lock_pts} onChange={(v) => set({ level_2_lock_pts: v })} disabled={disabled} />

        <Num label="Runner trigger" suffix="pts" min={0.5} max={500} step={0.5}
          hint="Once the option is this far above entry, the stop starts trailing. Not a take-profit: the trade stays open to keep running."
          value={e.target_pts} onChange={(v) => set({ target_pts: v })} disabled={disabled} />
        <Num label="Runner trails by" suffix="pts" min={0.5} max={500} step={0.5}
          hint="From then on the stop sits this far below the highest price reached. Only a new high moves it; a pullback never lowers it."
          value={e.level_3_runner_step_pts} onChange={(v) => set({ level_3_runner_step_pts: v })} disabled={disabled} />
      </div>

      {error ? (
        <SettingsError>{error}</SettingsError>
      ) : (
        <WorkedExample
          title={<>Example with these settings: an option bought at {rupees(EXAMPLE_ENTRY)}</>}
          footer="The bot sells when the bid falls to the stop, wherever it is at that moment. In a fast market the fill can land a little below it."
        >
          <li>
            The stop starts at <b>{rupees(EXAMPLE_ENTRY - e.stop_loss_pts)}</b>.
          </li>
          <li>
            The bid reaches {rupees(EXAMPLE_ENTRY + e.level_1_trigger_pts)}: the stop moves up to{" "}
            <b>{rupees(EXAMPLE_ENTRY + e.level_1_lock_pts)}</b>.
          </li>
          <li>
            The bid reaches {rupees(EXAMPLE_ENTRY + e.level_2_trigger_pts)}: the stop moves up to{" "}
            <b>{rupees(EXAMPLE_ENTRY + e.level_2_lock_pts)}</b>.
          </li>
          <li>
            The bid passes {rupees(EXAMPLE_ENTRY + e.target_pts)}: the stop trails {num(e.level_3_runner_step_pts)} pts
            behind the high — a high of {rupees(exampleHigh)} puts it at <b>{rupees(exampleTrail)}</b>.
          </li>
        </WorkedExample>
      )}
    </div>
  );
}

function FlyEntryFilter({
  fly,
  onConfig,
  disabled,
}: {
  fly: IronFlyScalperConfig;
  onConfig: (patch: Record<string, unknown>) => void;
  disabled: boolean;
}) {
  const f = fly.entry_filter;
  return (
    <div className="space-y-4">
      <TabIntro>
        <p>
          An optional extra check before every new fly, after the Re-entry conditions have cleared. A fly earns its
          credit when the market moves less than option prices expected; each filter skips a moment when that is
          visibly not happening. If a filter cannot read its input, the fly waits rather than opening blind.
        </p>
      </TabIntro>
      <Select
        label="Filter"
        value={f.kind}
        disabled={disabled}
        onChange={(kind) => onConfig({ entry_filter: { ...f, kind } })}
        options={[
          { value: "none", label: "None — the re-entry gate only" },
          { value: "vix_not_rising", label: "India VIX not rising" },
          { value: "signal_quiet", label: "Only while a signal is quiet" },
        ]}
      />
      {f.kind === "vix_not_rising" ? (
        <div className="grid gap-4 sm:grid-cols-2">
          <Num label="Look back" suffix="min" min={1} max={120}
            hint="How far back to compare India VIX against…"
            value={f.vix_lookback_minutes}
            onChange={(v) => onConfig({ entry_filter: { ...f, vix_lookback_minutes: v } })} disabled={disabled} />
          <Num label="Skip if VIX rose more than" suffix="%" min={0} max={50} step={0.25}
            hint="…and the rise in VIX over that time that holds the fly back. Rising VIX marks every sold option up at once. Read at most once a minute, and only when a fly would otherwise open."
            value={f.vix_max_rise_pct}
            onChange={(v) => onConfig({ entry_filter: { ...f, vix_max_rise_pct: v } })} disabled={disabled} />
        </div>
      ) : null}
      {f.kind === "signal_quiet" ? (
        <div className="space-y-2">
          <SignalChoicePicker
            value={f.signal}
            withDirection={false}
            disabled={disabled}
            onChange={(signal) => onConfig({ entry_filter: { ...f, signal } })}
          />
          <p className="text-hint text-faint">
            A live call on either side holds the fly: a move the signal thinks is under way is the opposite of the quiet
            a fly wants.
          </p>
        </div>
      ) : null}
    </div>
  );
}
