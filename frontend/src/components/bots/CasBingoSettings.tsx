"use client";

import { useContext, useEffect } from "react";

import { Checkbox } from "@/components/ui/Checkbox";
import { FieldValidityContext, NumberInput } from "@/components/ui/NumberInput";
import {
  CAS_BINGO_CREDIT_WARNING,
  CAS_BINGO_REGIME_NOTE,
  INDEX_LABEL,
  type CasBingoConfig,
  type CasBingoStrategy,
} from "@/lib/use-bots";

import type { Tab } from "@/components/bots/ScalperSettings";
import {
  SettingsError,
  TabIntro,
  WorkedExample,
  num,
  rupees,
} from "@/components/bots/SettingsHelp";
import { SignalChoicePicker } from "@/components/bots/SignalChoicePicker";

// Illustrative figures for the worked examples: strikes from a percentage need an index level,
// and target/stop rupees need a credit or debit. Round numbers keep the arithmetic visible.
const EXAMPLE_INDEX = 25_000;
const EXAMPLE_CREDIT = 10_000;

/** The backend's `_outer_beyond_inner`, said beside the fields instead of on Save. */
function legsOrderError(inner: number, outer: number): string | null {
  return outer <= inner
    ? `The outer leg must sit further out than the inner leg (${num(outer)}% is not beyond ${num(inner)}%).`
    : null;
}

function useReportValid(key: string, valid: boolean) {
  const report = useContext(FieldValidityContext);
  useEffect(() => {
    report?.(key, valid);
    return () => report?.(key, true);
  }, [report, key, valid]);
}

// Schedule first, the same id the other bots use for their timing tab. Every strategy block
// has its own tab because the manual sheet prices all five structures off all three.
export const CAS_BINGO_TABS: Tab[] = [
  { id: "schedule", label: "Schedule" },
  { id: "strategy", label: "Strategy" },
  { id: "credit", label: "Credit" },
  { id: "debit", label: "Debit" },
  { id: "strangle", label: "Strangle" },
  { id: "liquidation", label: "Liquidation" },
];

const STRATEGIES: { value: CasBingoStrategy; label: string; hint: string }[] = [
  {
    value: "credit_spread",
    label: "Credit spread",
    hint: "Before 15:15: after the index moves from the open, the signal flips against it — sell a CE after a rise, a PE after a drop. From 15:20: sell the side beyond the indicative index that is still priced when it should settle worthless.",
  },
  {
    value: "debit_spread",
    label: "Debit spread",
    hint: "A flip of the chosen signal, held for the sustain period: call spread when bullish, put spread when bearish (the other way when fading).",
  },
  {
    value: "long_strangle",
    label: "Long strangle",
    hint: "No signal. Buys a CE and a PE at a set time.",
  },
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
      <span className="block text-micro font-semibold uppercase tracking-[0.06em] text-faint">{label}</span>
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

function Time({
  label,
  value,
  onChange,
  disabled,
}: {
  label: string;
  value: string;
  onChange: (next: string) => void;
  disabled: boolean;
}) {
  return (
    <label className="block">
      <span className="block text-micro font-semibold uppercase tracking-[0.06em] text-faint">{label}</span>
      <input
        type="time"
        className="app-input mt-1 w-32 font-mono tabular-nums"
        aria-label={label}
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
      />
    </label>
  );
}

function Warning({ children }: { children: React.ReactNode }) {
  return (
    <p className="rounded-lg border border-down/30 bg-down-tint p-3 text-hint text-text">{children}</p>
  );
}

export function CasBingoSettings({
  tab,
  config,
  onConfig,
  disabled,
}: {
  tab: string;
  config: CasBingoConfig;
  onConfig: (patch: Partial<CasBingoConfig>) => void;
  disabled: boolean;
}) {
  const c = config.credit;
  const d = config.debit;
  const s = config.strangle;
  const l = config.liquidation;

  // Checked whichever tab is open: an invalid spread on a tab you have left still blocks Save.
  const creditError = legsOrderError(c.inner_pct, c.outer_pct);
  const debitError = legsOrderError(d.inner_pct, d.outer_pct);
  useReportValid("cas_credit_legs", creditError === null);
  useReportValid("cas_debit_legs", debitError === null);

  if (tab === "schedule") {
    return (
      <div className="space-y-5">
        <TabIntro>
          <p>
            On expiry day, continuous trading stops at 15:15 and the exchange runs a <b>closing auction session
            (CAS)</b>: orders are collected 15:20–15:30 and matched by 15:35, and the day&rsquo;s options settle on
            the auction price, which can move sharply. This bot trades the last stretch before and inside that
            auction, entering at most once per index per day.
          </p>
        </TabIntro>
        <div>
          <p className="text-micro font-semibold uppercase tracking-[0.06em] text-faint">Indices</p>
          <p className="mt-1 text-hint text-faint">
            Fires only on an index&apos;s own expiry day, read from the scrip master.
          </p>
          <div className="mt-2 flex gap-5">
            {["NIFTY", "BSESEN"].map((code) => (
              <label key={code} className="flex items-center gap-2 text-body">
                <Checkbox
                  checked={Boolean(config.indices?.[code]?.enabled)}
                  disabled={disabled}
                  onChange={(checked) =>
                    onConfig({ indices: { ...config.indices, [code]: { enabled: checked } } })
                  }
                />
                {INDEX_LABEL[code] ?? code}
              </label>
            ))}
          </div>
        </div>
        <div>
          <p className="text-micro font-semibold uppercase tracking-[0.06em] text-faint">Pre-CAS window</p>
          <p className="mt-1 text-hint text-faint">The last stretch of normal trading, when a signal can still be read.</p>
          <div className="mt-1 flex gap-3">
            <Time label="Start" value={config.pre_cas_window.start} disabled={disabled} onChange={(v) => onConfig({ pre_cas_window: { ...config.pre_cas_window, start: v } })} />
            <Time label="End" value={config.pre_cas_window.end} disabled={disabled} onChange={(v) => onConfig({ pre_cas_window: { ...config.pre_cas_window, end: v } })} />
          </div>
        </div>
        <div>
          <p className="text-micro font-semibold uppercase tracking-[0.06em] text-faint">CAS window</p>
          <p className="mt-1 text-hint text-faint">The auction itself. It ends at 15:29 to leave an entry time to fill.</p>
          <div className="mt-1 flex gap-3">
            <Time label="Start" value={config.cas_window.start} disabled={disabled} onChange={(v) => onConfig({ cas_window: { ...config.cas_window, start: v } })} />
            <Time label="End" value={config.cas_window.end} disabled={disabled} onChange={(v) => onConfig({ cas_window: { ...config.cas_window, end: v } })} />
          </div>
          <p className="mt-1.5 text-hint text-faint">
            Both windows are entry windows. Signals stop at 15:15, when the closing auction
            begins, so inside the CAS window only the credit spread&rsquo;s auction rule (which reads
            no signal) and the strangle&rsquo;s clock can enter.
          </p>
        </div>
        <p className="text-hint text-faint">{CAS_BINGO_REGIME_NOTE}</p>
      </div>
    );
  }

  if (tab === "strategy") {
    return (
      <div className="space-y-3" role="radiogroup" aria-label="Strategy">
        <TabIntro>
          <p>
            Which structure the bot trades on its own, in Simulation and Autonomous. Each has its own settings tab.
            A run you start by hand always prices all five structures side by side.
          </p>
        </TabIntro>
        {STRATEGIES.map((option) => (
          <label key={option.value} className="flex items-start gap-2.5 rounded-lg border border-border p-3">
            <input
              type="radio"
              name="cas-bingo-strategy"
              className="mt-1 accent-accent"
              checked={config.strategy === option.value}
              disabled={disabled}
              onChange={() => onConfig({ strategy: option.value })}
            />
            <span>
              <span className="block text-body font-semibold text-text">{option.label}</span>
              <span className="mt-0.5 block text-hint text-faint">{option.hint}</span>
            </span>
          </label>
        ))}
        {config.strategy === "credit_spread" && (
          <Warning>
            <strong>Warning:</strong> {CAS_BINGO_CREDIT_WARNING}
          </Warning>
        )}
        {config.strategy !== "long_strangle" ? (
          <div className="rounded-lg border border-border p-3">
            <p className="mb-2 text-micro font-semibold uppercase tracking-[0.06em] text-faint">Signal the spreads read</p>
            <SignalChoicePicker
              value={config.signal}
              disabled={disabled}
              withDirection={config.strategy === "debit_spread"}
              directionHint="Fading buys the spread against the flip. The credit spread already sells against the move, so it has no direction to choose."
              onChange={(signal) => onConfig({ signal })}
            />
          </div>
        ) : null}
        <p className="text-hint text-faint">Every structure buys its long leg first and sells only once it has filled.</p>
      </div>
    );
  }

  if (tab === "credit") {
    const patch = (p: Partial<CasBingoConfig["credit"]>) => onConfig({ credit: { ...c, ...p } });
    const width = c.outer_pct - c.inner_pct;
    return (
      <div className="space-y-4">
        <TabIntro>
          <p>
            A <b>credit spread</b> sells one option and buys a cheaper one further out as a hedge. You collect a
            credit up front and keep it if the sold option expires worthless. The most it can lose is the gap between
            the two strikes (its <b>width</b>) minus the credit. This one bets the index swings back towards where it
            opened.
          </p>
        </TabIntro>
        <Warning>
          <strong>Warning:</strong> {CAS_BINGO_CREDIT_WARNING}
        </Warning>
        <Num label="Margin to deploy" suffix="lakh" step={0.1} min={0.1} max={1000} value={c.margin_lakhs} disabled={disabled} onChange={(v) => patch({ margin_lakhs: v })} hint="The most margin the spread may use; it sells as many lots as fit. 1 lakh = ₹1,00,000." />
        <p className="text-micro font-semibold uppercase tracking-[0.06em] text-faint">Before the auction (pre-CAS window)</p>
        <Num label="Move from open that arms it" suffix="%" step={0.05} min={0.05} max={10} value={c.move_trigger_pct} disabled={disabled} onChange={(v) => patch({ move_trigger_pct: v })} hint="The index must have risen (or dropped) this far from the day's open at the flip." />
        <Num label="Inner (sold) leg from open" suffix="%" step={0.05} min={0} max={20} value={c.inner_pct} disabled={disabled} onChange={(v) => patch({ inner_pct: v })} hint="Measured from the day's open, not spot — so after a big move the sold leg can be in the money." />
        <Num label="Outer (bought) leg from open" suffix="%" step={0.05} min={0.05} max={25} value={c.outer_pct} disabled={disabled} onChange={(v) => patch({ outer_pct: v })} hint="Outer minus inner is the spread's width, used in both windows." />
        <p className="text-micro font-semibold uppercase tracking-[0.06em] text-faint">Inside the auction (from 15:20)</p>
        <Num label="Gap beyond the indicative index" suffix="%" step={0.05} min={0.05} max={10} value={c.auction_gap_pct} disabled={disabled} onChange={(v) => patch({ auction_gap_pct: v })} hint="From 15:20 the exchange's indicative index is roughly where expiry will settle. The sold leg sits at least this far above it after a rise (below it after a drop). SENSEX has swung 2–3% inside the auction." />
        <Num label="Minimum credit" suffix="% of width" step={1} min={1} max={100} value={c.auction_min_credit_pct} disabled={disabled} onChange={(v) => patch({ auction_min_credit_pct: v })} hint="Sell only while the spread still pays at least this share of its width — an option that should expire worthless but still costs this much is the spike being faded." />
        <p className="text-micro font-semibold uppercase tracking-[0.06em] text-faint">Exits (both windows)</p>
        <Num label="Profit target" suffix="% of credit" min={1} max={100} value={c.target_pct} disabled={disabled} onChange={(v) => patch({ target_pct: v })} hint="Buys the spread back once this share of the credit is earned." />
        <Num label="Stop-loss" suffix="% of credit" min={1} max={1000} value={c.stop_loss_pct} disabled={disabled} onChange={(v) => patch({ stop_loss_pct: v })} hint="Closes once the loss reaches this share of the credit. If neither fires, the spread settles at expiry." />
        {creditError ? (
          <SettingsError>{creditError}</SettingsError>
        ) : (
          <WorkedExample
            title={<>Example with these settings: the index opened at {num(EXAMPLE_INDEX, 0)}</>}
            footer="Before the auction a falling index mirrors this with puts below the open. Strikes are rounded to listed ones."
          >
            <li>
              Before 15:15: once the index is up {num(c.move_trigger_pct)}% (at{" "}
              {num(EXAMPLE_INDEX * (1 + c.move_trigger_pct / 100), 0)}) and the signal turns bearish, it buys the{" "}
              <b>{num(EXAMPLE_INDEX * (1 + c.outer_pct / 100), 0)} CE</b> and then sells the{" "}
              <b>{num(EXAMPLE_INDEX * (1 + c.inner_pct / 100), 0)} CE</b> — a spread{" "}
              {num((EXAMPLE_INDEX * width) / 100, 0)} points wide.
            </li>
            <li>
              From 15:20, on a day the index has risen, it sells a call at least {num(c.auction_gap_pct)}% above the
              indicative index — at or above <b>{num(EXAMPLE_INDEX * (1 + c.auction_gap_pct / 100), 0)}</b> if that reads{" "}
              {num(EXAMPLE_INDEX, 0)} — only while the spread still pays about{" "}
              {num((EXAMPLE_INDEX * width * c.auction_min_credit_pct) / 10_000, 0)} points or more.
            </li>
            <li>
              On {rupees(EXAMPLE_CREDIT, 0)} of credit, it books at <b>+{rupees((EXAMPLE_CREDIT * c.target_pct) / 100, 0)}</b>{" "}
              and stops at <b>−{rupees((EXAMPLE_CREDIT * c.stop_loss_pct) / 100, 0)}</b>.
            </li>
          </WorkedExample>
        )}
      </div>
    );
  }

  if (tab === "debit") {
    const patch = (p: Partial<CasBingoConfig["debit"]>) => onConfig({ debit: { ...d, ...p } });
    return (
      <div className="space-y-4">
        <TabIntro>
          <p>
            A <b>debit spread</b> buys an option near the index and sells one further out, paying a net premium (the{" "}
            <b>debit</b>). It profits if the index moves the bot&rsquo;s way, and the debit is the most it can lose.
            Bullish buys a call spread; bearish a put spread.
          </p>
        </TabIntro>
        <Num label="Net premium to pay" suffix="₹" step={500} min={1} max={10_000_000} value={d.premium_budget_inr} disabled={disabled} onChange={(v) => patch({ premium_budget_inr: v })} hint="The most to spend on one spread. Lots = this ÷ the cost of one lot, rounded down; it skips if one lot costs more." />
        <Num label="Sustained for" suffix="min" step={0.5} min={0} max={60} value={d.sustain_minutes} disabled={disabled} onChange={(v) => patch({ sustain_minutes: v })} hint="How long the flip must hold before the spread is bought. The signal is chosen on the Strategy tab." />
        <Num label="Inner (bought) leg from spot" suffix="%" step={0.05} min={0} max={20} value={d.inner_pct} disabled={disabled} onChange={(v) => patch({ inner_pct: v })} hint="0 means at the money. Measured from spot when it deploys." />
        <Num label="Outer (sold) leg from spot" suffix="%" step={0.05} min={0.05} max={25} value={d.outer_pct} disabled={disabled} onChange={(v) => patch({ outer_pct: v })} hint="Selling it lowers the cost, but caps the profit at this strike." />
        <Num label="Profit target" suffix="% of debit" min={1} max={1000} value={d.target_pct} disabled={disabled} onChange={(v) => patch({ target_pct: v })} hint="Sells once the gain reaches this share of what was paid. 100% means it has doubled." />
        <Num label="Stop-loss" suffix="% of debit" min={1} max={100} value={d.stop_loss_pct} disabled={disabled} onChange={(v) => patch({ stop_loss_pct: v })} hint="Sells once the loss reaches this share of what was paid." />
        {debitError ? (
          <SettingsError>{debitError}</SettingsError>
        ) : (
          <WorkedExample
            title={<>Example with these settings: the index at {num(EXAMPLE_INDEX, 0)}, a bullish flip</>}
            footer="A bearish flip mirrors this with puts below the index. Strikes are rounded to listed ones."
          >
            <li>
              Once the flip has held for {num(d.sustain_minutes)} minute{d.sustain_minutes === 1 ? "" : "s"}, it buys the{" "}
              <b>{num(EXAMPLE_INDEX * (1 + d.inner_pct / 100), 0)} CE</b> and then sells the{" "}
              <b>{num(EXAMPLE_INDEX * (1 + d.outer_pct / 100), 0)} CE</b>.
            </li>
            <li>
              Paying {rupees(d.premium_budget_inr, 0)}, it books at <b>+{rupees((d.premium_budget_inr * d.target_pct) / 100, 0)}</b>{" "}
              and stops at <b>−{rupees((d.premium_budget_inr * d.stop_loss_pct) / 100, 0)}</b>.
            </li>
          </WorkedExample>
        )}
      </div>
    );
  }

  if (tab === "strangle") {
    const patch = (p: Partial<CasBingoConfig["strangle"]>) => onConfig({ strangle: { ...s, ...p } });
    return (
      <div className="space-y-4">
        <TabIntro>
          <p>
            A <b>long strangle</b>{" "}buys a call above the index and a put below it. It profits from a big move either
            way, which suits the auction&rsquo;s sharp swings, and the premium paid is the most it can lose. It reads no
            signal: it simply buys at the entry time.
          </p>
        </TabIntro>
        <Num label="Premium to pay" suffix="₹" step={500} min={1} max={10_000_000} value={s.premium_budget_inr} disabled={disabled} onChange={(v) => patch({ premium_budget_inr: v })} hint="The most to spend on both options together. Lots = this ÷ the cost of one lot, rounded down." />
        <Time label="Entry time" value={s.entry_time_ist} disabled={disabled} onChange={(v) => patch({ entry_time_ist: v })} />
        <Num label="CE distance from spot" suffix="%" step={0.05} min={0} max={20} value={s.call_pct} disabled={disabled} onChange={(v) => patch({ call_pct: v })} hint="How far above the index the call's strike sits. 0 means at the money." />
        <Num label="PE distance from spot" suffix="%" step={0.05} min={0} max={20} value={s.put_pct} disabled={disabled} onChange={(v) => patch({ put_pct: v })} hint="How far below the index the put's strike sits." />
        <Num label="Profit target" suffix="% of debit" min={1} max={1000} value={s.target_pct} disabled={disabled} onChange={(v) => patch({ target_pct: v })} hint="Sells once the gain reaches this share of what was paid. 100% means it has doubled." />
        <Num label="Stop-loss" suffix="% of debit" min={1} max={100} value={s.stop_loss_pct} disabled={disabled} onChange={(v) => patch({ stop_loss_pct: v })} hint="Sells once the loss reaches this share of what was paid." />
        <WorkedExample title={<>Example with these settings: the index at {num(EXAMPLE_INDEX, 0)}</>}>
          <li>
            At <b>{s.entry_time_ist}</b> it buys the <b>{num(EXAMPLE_INDEX * (1 + s.call_pct / 100), 0)} CE</b> and the{" "}
            <b>{num(EXAMPLE_INDEX * (1 - s.put_pct / 100), 0)} PE</b> (rounded to listed strikes).
          </li>
          <li>
            Paying {rupees(s.premium_budget_inr, 0)}, it books at <b>+{rupees((s.premium_budget_inr * s.target_pct) / 100, 0)}</b>{" "}
            and stops at <b>−{rupees((s.premium_budget_inr * s.stop_loss_pct) / 100, 0)}</b>.
          </li>
        </WorkedExample>
      </div>
    );
  }

  if (tab === "liquidation") {
    const patch = (p: Partial<CasBingoConfig["liquidation"]>) => onConfig({ liquidation: { ...l, ...p } });
    return (
      <div className="space-y-4">
        <TabIntro>
          <p>
            Used only when the structure the bot wants to enter needs more margin than you have free. It can then buy
            back some of your own short options that have already earned most of their premium, which releases their
            margin. It never touches a short that would be bought back at a loss.
          </p>
        </TabIntro>
        <label className="flex items-start gap-2">
          <Checkbox checked={l.enabled} disabled={disabled} onChange={(v) => patch({ enabled: v })} className="mt-0.5" />
          <span>
            <span className="block text-body text-text">Free margin by buying back profitable shorts</span>
            <span className="mt-0.5 block text-hint text-faint">
              Only shorts on the same index and today&apos;s expiry, most premium captured first,
              just enough lots to cover the shortfall.
            </span>
          </span>
        </label>
        <Num label="Minimum premium captured" suffix="%" min={1} max={99} value={l.min_captured_pct} disabled={disabled || !l.enabled} onChange={(v) => patch({ min_captured_pct: v })} hint="80% buys back a short sold at ₹100 only at ₹20 or less — every liquidation is a profitable one." />
        <Num label="Safety buffer" suffix="%" min={0} max={100} value={l.safety_buffer_pct} disabled={disabled || !l.enabled} onChange={(v) => patch({ safety_buffer_pct: v })} hint="Added to the shortfall: the in-app margin estimate can run below ICICI's on short calls." />
      </div>
    );
  }

  return null;
}
