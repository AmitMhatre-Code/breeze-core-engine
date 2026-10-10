"use client";

import { useContext, useEffect } from "react";

import { SettingsError, TabIntro, WorkedExample, num, rupees } from "@/components/bots/SettingsHelp";
import { FieldValidityContext, NumberInput } from "@/components/ui/NumberInput";
import { Select, type SelectOption } from "@/components/ui/Select";
import type { CondorSettings as Campaign } from "@/lib/condor";
import type { Tab } from "@/components/bots/ScalperSettings";
import { PremiumGateSettings } from "@/components/bots/PremiumGateSettings";

/** The Dynamic Iron Condor bot's settings, in the drawer every bot uses (#67). Its campaign
 *  settings are the same ones a manual campaign started from Basket Orders runs on. */
export const CONDOR_TABS: Tab[] = [
  { id: "cycle", label: "Cycle" },
  { id: "strikes", label: "Strikes" },
  { id: "rolls", label: "Rolls" },
  { id: "risk", label: "Risk" },
  { id: "premium", label: "Premium" },
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

/** `CondorSettings._clock_is_ordered` and `_has_a_stop`, said beside the fields instead of on Save. */
function clockError(c: Campaign): string | null {
  if (!(c.entry_dte >= c.tranche_cutoff_dte && c.tranche_cutoff_dte >= c.exit_dte)) {
    return "Entry DTE must be at or above the tranche cut-off DTE, which must be at or above the exit DTE.";
  }
  if (c.tranche_cutoff_dte === c.exit_dte && c.tranches > 1) {
    return "Tranches need a cut-off DTE above the exit DTE.";
  }
  return null;
}

function stopError(c: Campaign): string | null {
  return c.max_loss_inr == null && c.max_loss_pct_of_ceiling == null
    ? "Set a max loss in rupees, as a % of the margin ceiling, or both."
    : null;
}

/** DTE at or below which each tranche is due: `strikes.tranche_due_dte`, evenly spaced from the
 *  entry DTE, the last one a full step before the cut-off. */
function trancheDtes(c: Campaign): number[] {
  const spacing = (c.entry_dte - c.tranche_cutoff_dte) / c.tranches;
  return Array.from({ length: c.tranches }, (_, i) => c.entry_dte - i * spacing);
}

// Illustrative index level for the Strikes example: a percentage of spot needs a spot.
const EXAMPLE_INDEX = 25_000;

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

  // Checked whichever tab is open: an invalid clock on a tab you have left still blocks Save.
  const clock = clockError(c);
  const stop = stopError(c);
  const report = useContext(FieldValidityContext);
  useEffect(() => {
    report?.("condor_clock", clock === null);
    report?.("condor_stop", stop === null);
    return () => {
      report?.("condor_clock", true);
      report?.("condor_stop", true);
    };
  }, [report, clock, stop]);

  const evidence = (
    <p className="text-hint text-faint">
      Any change here needs a new Simulation cycle before Semi-auto or Auto unlock again, and hands a campaign
      it is running back to you. Manual campaigns started from Basket Orders also use these campaign settings.
    </p>
  );

  if (tab === "cycle") {
    const dues = trancheDtes(c);
    return (
      <div className="space-y-4">
        <TabIntro>
          <p>
            The bot runs NIFTY iron condors as a <b>campaign</b> of cycles, one expiry at a time. Each cycle opens in{" "}
            <b>tranches</b>{" "}— several smaller entries a few days apart, so one bad day&rsquo;s prices do not set the
            whole position — and then exits, or rolls into the next cycle, as expiry approaches. <b>DTE</b> is calendar
            days to expiry. It acts only at its two daily checks.
          </p>
        </TabIntro>
        {evidence}
        <Choice id="condor-expiry-kind" label="Expiries" hint="Monthly uses each month's last expiry, where far-dated options trade most; Any allows weeklies too." value={c.expiry_kind} options={EXPIRY_KINDS} disabled={disabled} onChange={(v) => set({ expiry_kind: v })} />
        <Num label="Entry DTE" suffix="days" min={0} max={120} value={c.entry_dte} disabled={disabled} onChange={(v) => set({ entry_dte: v })} hint="The first tranche goes in at or below this many days to expiry." />
        <Num label="Tranche cut-off DTE" suffix="days" min={0} max={120} value={c.tranche_cutoff_dte} disabled={disabled} onChange={(v) => set({ tranche_cutoff_dte: v })} hint="No new tranche below this." />
        <Num label="Exit DTE" suffix="days" min={0} max={120} value={c.exit_dte} disabled={disabled} onChange={(v) => set({ exit_dte: v })} hint="The cycle closes (or rolls on, per the Bot tab) at or below this. Close to expiry, a short option's price swings hardest." />
        <Num label="Tranches" min={1} max={10} value={c.tranches} disabled={disabled} onChange={(v) => set({ tranches: v })} hint="Entries spread evenly between the entry and cut-off DTE, all on the same expiry." />
        <Choice id="condor-entry-check" label="Enter tranches at" hint="Which of the two daily checks opens a tranche that is due." value={c.entry_check} options={ENTRY_CHECKS} disabled={disabled} onChange={(v) => set({ entry_check: v })} />
        <div className="flex gap-3">
          <Time label="Start-of-day check" value={c.sod_check_ist} disabled={disabled} onChange={(v) => set({ sod_check_ist: v })} />
          <Time label="End-of-day check" value={c.eod_check_ist} disabled={disabled} onChange={(v) => set({ eod_check_ist: v })} />
        </div>
        <p className="text-hint text-faint">
          IST. Prices are ignored before the start-of-day check, which also catches overnight gaps; the end-of-day
          check runs after the closing auction, while options still trade until 15:40.
        </p>
        {clock ? (
          <SettingsError>{clock}</SettingsError>
        ) : (
          <WorkedExample title="With these settings, each cycle">
            {dues.map((dte, i) => (
              <li key={i}>
                Tranche {i + 1} of {c.tranches} goes in at the first {c.entry_check === "sod" ? "start-of-day" : "end-of-day"}{" "}
                check at or below <b>{num(dte, 1)} DTE</b>.
              </li>
            ))}
            <li>
              No new tranche below {c.tranche_cutoff_dte} DTE; the cycle exits or rolls at <b>{c.exit_dte} DTE</b>.
            </li>
          </WorkedExample>
        )}
      </div>
    );
  }

  if (tab === "strikes") {
    const wingPts = (EXAMPLE_INDEX * c.wing_width_pct) / 100;
    return (
      <div className="space-y-4">
        <TabIntro>
          <p>
            An <b>iron condor</b> sells a call above the index and a put below it, collecting a credit, and buys a
            further-out call and put as <b>wings</b> that cap the loss. It earns while NIFTY stays between the two sold
            strikes.
          </p>
        </TabIntro>
        {evidence}
        <Num label="Short Δ" step={0.01} min={0.01} max={0.49} value={c.short_delta} disabled={disabled} onChange={(v) => set({ short_delta: v })} hint="Which strikes to sell, by delta (Δ): how much the option's price moves per point of NIFTY, and roughly its chance of ending in the money. 0.20 sells options with about a 1-in-5 chance; lower is further out, safer, and pays less." />
        <Num label="Wing width" suffix="% of spot" step={0.1} min={0.1} max={25} value={c.wing_width_pct} disabled={disabled} onChange={(v) => set({ wing_width_pct: v })} hint="Each wing this far beyond its short, the same on both sides, snapped outward to a listed strike. When the listed strikes end first, the furthest one is used and the suggestion says so. 4.5% is about 1,000 NIFTY points at 22,400." />
        <WorkedExample title={<>Example with these settings: NIFTY at {num(EXAMPLE_INDEX, 0)}</>}>
          <li>
            It sells the call and the put whose delta is closest to <b>{num(c.short_delta)}</b> — strikes set by the
            option prices that day, not by a fixed distance.
          </li>
          <li>
            Each wing sits about <b>{num(wingPts, 0)} points</b> beyond its short. The most a cycle can lose is that
            width, less the credit collected, per unit.
          </li>
        </WorkedExample>
      </div>
    );
  }

  if (tab === "rolls") {
    return (
      <div className="space-y-4">
        <TabIntro>
          <p>
            When NIFTY moves, the side it moves towards is <b>tested</b>; the other side is <b>untested</b>, and its
            short loses value. A <b>roll</b>{" "}buys back the untested short and sells a new one closer to the index, at
            the tested short&rsquo;s delta (never past the tested strike), with a new wing. That collects more credit
            and re-centres the condor. Any rule below can trigger it at a daily check.
          </p>
        </TabIntro>
        {evidence}
        <Num label="Untested side below Δ" step={0.01} min={0.01} max={0.49} value={c.leg_rule_delta_floor} disabled={disabled} onChange={(v) => set({ leg_rule_delta_floor: v })} hint="Roll the untested side when its short falls under this." />
        <Num label="…or decayed" suffix="%" min={1} max={100} value={c.leg_rule_decay_pct} disabled={disabled} onChange={(v) => set({ leg_rule_decay_pct: v })} hint="…or when it has lost this share of its premium." />
        <Num label="Net Δ band per lot" step={0.01} min={0.01} max={1} value={c.net_delta_band_per_lot} disabled={disabled} onChange={(v) => set({ net_delta_band_per_lot: v })} hint="Net delta is how much the whole position gains or loses per point of NIFTY. Roll when, per lot, it drifts outside ±this — the condor has become a bet on direction." />
        <Num label="Minimum roll credit" suffix="points" min={0} max={1000} value={c.min_roll_credit_points} disabled={disabled} onChange={(v) => set({ min_roll_credit_points: v })} hint="A roll adding less than this per unit, after charges, is skipped and reported." />
        <Num label="No rolls within" suffix="days of exit" min={0} max={30} value={c.no_roll_within_days_of_exit} disabled={disabled} onChange={(v) => set({ no_roll_within_days_of_exit: v })} hint="A roll due this close to the exit DTE is reported, not done. 0 = off." />
      </div>
    );
  }

  if (tab === "risk") {
    const limits = [
      c.max_loss_inr,
      c.max_loss_pct_of_ceiling != null ? (c.margin_ceiling_inr * c.max_loss_pct_of_ceiling) / 100 : null,
    ].filter((v): v is number => v != null);
    return (
      <div className="space-y-4">
        <TabIntro>
          <p>
            How much capital the campaign may tie up, and the loss at which it closes everything. At least one of the
            two max-loss limits must be set.
          </p>
        </TabIntro>
        {evidence}
        <Num label="Margin ceiling" suffix="₹" step={10_000} min={1} max={100_000_000} value={c.margin_ceiling_inr} disabled={disabled} onChange={(v) => set({ margin_ceiling_inr: v })} hint="The campaign's margin across all its tranches; the rest of your capital is the buffer." />
        <OptionalNum label="Max loss" suffix="₹" step={1000} min={1} max={100_000_000} blank="off" value={c.max_loss_inr} disabled={disabled} onChange={(v) => set({ max_loss_inr: v })} hint="Close everything past this loss, checked at the two daily checks. Blank = off." />
        <OptionalNum label="…or of the ceiling" suffix="%" step={0.5} min={0.1} max={100} blank="off" value={c.max_loss_pct_of_ceiling} disabled={disabled} onChange={(v) => set({ max_loss_pct_of_ceiling: v })} hint="The tighter of the two binds. Blank = off." />
        {stop ? (
          <SettingsError>{stop}</SettingsError>
        ) : (
          <WorkedExample title="With these settings">
            <li>
              The campaign closes every leg once it is down <b>{rupees(Math.min(...limits), 0)}</b>
              {limits.length > 1 ? " — the tighter of the two limits" : ""}, checked at the two daily checks.
            </li>
          </WorkedExample>
        )}
      </div>
    );
  }

  if (tab === "premium") {
    return (
      <div className="space-y-4">
        {evidence}
        <PremiumGateSettings
          gate={c.premium_gate ?? { enabled: false, threshold: 1 }}
          side="sell"
          disabled={disabled}
          onChange={(premium_gate) => set({ premium_gate })}
        />
        <p className="app-card-muted p-3 text-hint">
          For a condor the gate decides only when a <b>tranche</b> goes in, including the first one of a time
          roll&rsquo;s next cycle. A due tranche waits, check by check, until premium is rich or the cut-off DTE
          passes. Rolls, exits and the max-loss stop never wait for it. If it says no at a time roll, the old cycle
          still closes and the campaign ends there, as with <b>Close</b>; the next cycle&rsquo;s tranches go in when
          premium is rich.
        </p>
      </div>
    );
  }

  if (tab === "bot") {
    return (
      <div className="space-y-4">
        <TabIntro>
          <p>
            How the bot itself runs the campaign. The other tabs are the campaign&rsquo;s rules, shared with
            campaigns you manage by hand.
          </p>
        </TabIntro>
        <Choice id="condor-exit-action" label="At the exit DTE" value={config.exit_action} options={EXIT_ACTIONS} disabled={disabled} onChange={(v) => onConfig({ exit_action: v })} hint="Time-roll closes the cycle and opens the next expiry's in one ticket. Close ends the campaign, and the bot starts a new one when the next cycle's first tranche is due. Part of what the bot's evidence is matched on, like the campaign settings." />
        <OptionalNum label="Lots per tranche" step={1} min={1} max={500} blank="from margin" value={config.lots_per_tranche} disabled={disabled} onChange={(v) => onConfig({ lots_per_tranche: v == null ? null : Math.round(v) })} hint="Blank sizes each tranche from today's margin: the ceiling over the tranches. Also what the play button pre-fills." />
        <Num label="Approval window" suffix="min" min={2} max={60} value={config.proposal_ttl_minutes} disabled={disabled} onChange={(v) => onConfig({ proposal_ttl_minutes: v })} hint="In Semi-auto, how long a proposal waits for your tap on Telegram before it lapses." />
      </div>
    );
  }

  return null;
}
