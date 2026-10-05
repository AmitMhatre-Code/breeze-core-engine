"use client";

import { NumberInput } from "@/components/ui/NumberInput";
import { Select, type SelectOption } from "@/components/ui/Select";
import type { CondorSettings as Campaign } from "@/lib/condor";
import type { Tab } from "@/components/bots/ScalperSettings";

/** The Dynamic Iron Condor bot's settings, in the drawer every bot uses (#67). Its campaign
 *  settings are the same ones a manual campaign started from Basket Orders runs on. */
export const CONDOR_TABS: Tab[] = [
  { id: "cycle", label: "Cycle" },
  { id: "strikes", label: "Strikes" },
  { id: "rolls", label: "Rolls" },
  { id: "risk", label: "Risk" },
  { id: "bot", label: "Bot" },
];

export type CondorBotConfig = {
  mode: "paper" | "telegram" | "auto";
  campaign: Campaign;
  exit_action: "time_roll" | "close";
  lots_per_tranche: number | null;
  proposal_ttl_minutes: number;
  paused: boolean;
  paused_reason: string | null;
};

const EXPIRY_KINDS = [
  { value: "monthly", label: "Monthly only" },
  { value: "any", label: "Any (weeklies too)" },
] as const satisfies ReadonlyArray<SelectOption<Campaign["expiry_kind"]>>;

const ENTRY_CHECKS = [
  { value: "sod", label: "Start-of-day check" },
  { value: "eod", label: "End-of-day check" },
] as const satisfies ReadonlyArray<SelectOption<Campaign["entry_check"]>>;

const EXIT_ACTIONS = [
  { value: "time_roll", label: "Time-roll to the next cycle" },
  { value: "close", label: "Close; start again by schedule" },
] as const satisfies ReadonlyArray<SelectOption<CondorBotConfig["exit_action"]>>;

function Label({ children, id }: { children: React.ReactNode; id?: string }) {
  return (
    <span id={id} className="block text-micro font-semibold uppercase tracking-[0.06em] text-faint">
      {children}
    </span>
  );
}

function Hint({ children }: { children?: React.ReactNode }) {
  return children ? <span className="mt-1 block text-hint text-faint">{children}</span> : null;
}

function Num({
  label,
  hint,
  value,
  onChange,
  disabled,
  min,
  max,
  step = 1,
  suffix,
}: {
  label: string;
  hint?: string;
  value: number;
  onChange: (next: number) => void;
  disabled: boolean;
  min: number;
  max: number;
  step?: number;
  suffix?: string;
}) {
  return (
    <label className="block">
      <Label>{label}</Label>
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
      <Hint>{hint}</Hint>
    </label>
  );
}

/** A number that may be left blank: blank is a choice here ("off", "from margin"), not a typo. */
function OptionalNum({
  label,
  hint,
  value,
  onChange,
  disabled,
  min,
  max,
  step = 1,
  suffix,
  blank,
}: {
  label: string;
  hint?: string;
  value: number | null;
  onChange: (next: number | null) => void;
  disabled: boolean;
  min: number;
  max: number;
  step?: number;
  suffix?: string;
  blank: string;
}) {
  return (
    <label className="block">
      <Label>{label}</Label>
      <div className="mt-1 flex items-center gap-2">
        <input
          type="number"
          inputMode="decimal"
          aria-label={label}
          min={min}
          max={max}
          step={step}
          disabled={disabled}
          placeholder={blank}
          value={value ?? ""}
          onChange={(e) => {
            const text = e.target.value.trim();
            if (text === "") return onChange(null);
            const n = Number(text);
            if (Number.isFinite(n) && n >= min && n <= max) onChange(n);
          }}
          className="app-input w-32 font-mono tabular-nums placeholder:text-faint"
        />
        {suffix && <span className="text-hint text-faint">{suffix}</span>}
      </div>
      <Hint>{hint}</Hint>
    </label>
  );
}

function Time({
  label,
  hint,
  value,
  onChange,
  disabled,
}: {
  label: string;
  hint?: string;
  value: string;
  onChange: (next: string) => void;
  disabled: boolean;
}) {
  return (
    <label className="block">
      <Label>{label}</Label>
      <input
        type="time"
        className="app-input mt-1 w-32 font-mono tabular-nums"
        aria-label={label}
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
      />
      <Hint>{hint}</Hint>
    </label>
  );
}

function Choice<T extends string>({
  id,
  label,
  hint,
  value,
  options,
  onChange,
  disabled,
}: {
  id: string;
  label: string;
  hint?: string;
  value: T;
  options: ReadonlyArray<SelectOption<T>>;
  onChange: (next: T) => void;
  disabled: boolean;
}) {
  return (
    <div className="block">
      <Label id={id}>{label}</Label>
      <div className="mt-1 max-w-xs">
        <Select value={value} options={options} onChange={onChange} disabled={disabled} labelledBy={id} />
      </div>
      <Hint>{hint}</Hint>
    </div>
  );
}

export function CondorSettings({
  tab,
  config,
  onConfig,
  disabled,
}: {
  tab: string;
  config: CondorBotConfig;
  onConfig: (patch: Partial<CondorBotConfig>) => void;
  disabled: boolean;
}) {
  const c = config.campaign;
  const set = (patch: Partial<Campaign>) => onConfig({ campaign: { ...c, ...patch } });
  const evidence = (
    <p className="text-hint text-faint">
      Any change here needs a new backtest before the bot can run, and hands a campaign it is running back to
      you. Manual campaigns started from Basket Orders also use these campaign settings.
    </p>
  );

  if (tab === "cycle") {
    return (
      <div className="space-y-4">
        {evidence}
        <Choice id="condor-expiry-kind" label="Expiries" value={c.expiry_kind} options={EXPIRY_KINDS} disabled={disabled} onChange={(v) => set({ expiry_kind: v })} />
        <Num label="Entry DTE" suffix="days" min={0} max={120} value={c.entry_dte} disabled={disabled} onChange={(v) => set({ entry_dte: v })} hint="The first tranche goes in at or below this many days to expiry." />
        <Num label="Tranche cut-off DTE" suffix="days" min={0} max={120} value={c.tranche_cutoff_dte} disabled={disabled} onChange={(v) => set({ tranche_cutoff_dte: v })} hint="No new tranche below this." />
        <Num label="Exit DTE" suffix="days" min={0} max={120} value={c.exit_dte} disabled={disabled} onChange={(v) => set({ exit_dte: v })} hint="Exit or time-roll at or below this." />
        <Num label="Tranches" min={1} max={10} value={c.tranches} disabled={disabled} onChange={(v) => set({ tranches: v })} hint="Entries spread evenly between the entry and cut-off DTE, all on the same expiry." />
        <Choice id="condor-entry-check" label="Enter tranches at" value={c.entry_check} options={ENTRY_CHECKS} disabled={disabled} onChange={(v) => set({ entry_check: v })} />
        <div className="flex gap-3">
          <Time label="Start-of-day check" value={c.sod_check_ist} disabled={disabled} onChange={(v) => set({ sod_check_ist: v })} />
          <Time label="End-of-day check" value={c.eod_check_ist} disabled={disabled} onChange={(v) => set({ eod_check_ist: v })} />
        </div>
        <p className="text-hint text-faint">
          IST. Prices are ignored before the start-of-day check, which also catches overnight gaps; the end-of-day
          check runs after the closing auction, while options still trade until 15:40.
        </p>
      </div>
    );
  }

  if (tab === "strikes") {
    return (
      <div className="space-y-4">
        {evidence}
        <Num label="Short Δ" step={0.01} min={0.01} max={0.49} value={c.short_delta} disabled={disabled} onChange={(v) => set({ short_delta: v })} hint="|Δ| of the shorts at entry (0.20 = 20 delta)." />
        <Num label="Wing Δ" step={0.01} min={0.01} max={0.49} value={c.wing_delta} disabled={disabled} onChange={(v) => set({ wing_delta: v })} hint="|Δ| of the wings, snapped outward, beyond the shorts." />
      </div>
    );
  }

  if (tab === "rolls") {
    return (
      <div className="space-y-4">
        {evidence}
        <Num label="Untested side below Δ" step={0.01} min={0.01} max={0.49} value={c.leg_rule_delta_floor} disabled={disabled} onChange={(v) => set({ leg_rule_delta_floor: v })} hint="Roll the untested side when its short falls under this." />
        <Num label="…or decayed" suffix="%" min={1} max={100} value={c.leg_rule_decay_pct} disabled={disabled} onChange={(v) => set({ leg_rule_decay_pct: v })} hint="…or when it has lost this share of its premium." />
        <Num label="Net Δ band per lot" step={0.01} min={0.01} max={1} value={c.net_delta_band_per_lot} disabled={disabled} onChange={(v) => set({ net_delta_band_per_lot: v })} hint="Roll when net delta per lot is outside ±this." />
        <Num label="Minimum roll credit" suffix="points" min={0} max={1000} value={c.min_roll_credit_points} disabled={disabled} onChange={(v) => set({ min_roll_credit_points: v })} hint="A roll adding less than this per unit, after charges, is skipped and reported." />
        <Num label="No rolls within" suffix="days of exit" min={0} max={30} value={c.no_roll_within_days_of_exit} disabled={disabled} onChange={(v) => set({ no_roll_within_days_of_exit: v })} hint="A roll due this close to the exit DTE is reported, not done. 0 = off." />
      </div>
    );
  }

  if (tab === "risk") {
    return (
      <div className="space-y-4">
        {evidence}
        <Num label="Margin ceiling" suffix="₹" step={10_000} min={1} max={100_000_000} value={c.margin_ceiling_inr} disabled={disabled} onChange={(v) => set({ margin_ceiling_inr: v })} hint="The campaign's margin across all its tranches; the rest of your capital is the buffer." />
        <OptionalNum label="Max loss" suffix="₹" step={1000} min={1} max={100_000_000} blank="off" value={c.max_loss_inr} disabled={disabled} onChange={(v) => set({ max_loss_inr: v })} hint="Close everything past this loss, checked at the two daily checks. Blank = off." />
        <OptionalNum label="…or of the ceiling" suffix="%" step={0.5} min={0.1} max={100} blank="off" value={c.max_loss_pct_of_ceiling} disabled={disabled} onChange={(v) => set({ max_loss_pct_of_ceiling: v })} hint="The tighter of the two binds. Blank = off." />
      </div>
    );
  }

  if (tab === "bot") {
    return (
      <div className="space-y-4">
        <Choice id="condor-exit-action" label="At the exit DTE" value={config.exit_action} options={EXIT_ACTIONS} disabled={disabled} onChange={(v) => onConfig({ exit_action: v })} hint="Part of what a backtest is matched on, like the campaign settings." />
        <OptionalNum label="Lots per tranche" step={1} min={1} max={500} blank="from margin" value={config.lots_per_tranche} disabled={disabled} onChange={(v) => onConfig({ lots_per_tranche: v == null ? null : Math.round(v) })} hint="Blank sizes each tranche from today's margin: the ceiling over the tranches. Also what the play button pre-fills." />
        <Num label="Approval window" suffix="min" min={2} max={60} value={config.proposal_ttl_minutes} disabled={disabled} onChange={(v) => onConfig({ proposal_ttl_minutes: v })} hint="In Telegram mode, how long a proposal waits for your tap before it lapses." />
      </div>
    );
  }

  return null;
}
