"use client";

import { Checkbox } from "@/components/ui/Checkbox";
import { NumberInput } from "@/components/ui/NumberInput";
import {
  CAS_BINGO_CREDIT_WARNING,
  CAS_BINGO_REGIME_NOTE,
  INDEX_LABEL,
  type CasBingoConfig,
  type CasBingoStrategy,
} from "@/lib/use-bots";

import type { Tab } from "@/components/bots/ScalperSettings";

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
    hint: "After the index moves from the open, the signal flips against it: sell a CE after a rise, a PE after a drop.",
  },
  {
    value: "debit_spread",
    label: "Debit spread",
    hint: "A strong signal flip, held for the sustain period: call spread when bullish, put spread when bearish.",
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

  if (tab === "schedule") {
    return (
      <div className="space-y-5">
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
          <div className="mt-1 flex gap-3">
            <Time label="Start" value={config.pre_cas_window.start} disabled={disabled} onChange={(v) => onConfig({ pre_cas_window: { ...config.pre_cas_window, start: v } })} />
            <Time label="End" value={config.pre_cas_window.end} disabled={disabled} onChange={(v) => onConfig({ pre_cas_window: { ...config.pre_cas_window, end: v } })} />
          </div>
        </div>
        <div>
          <p className="text-micro font-semibold uppercase tracking-[0.06em] text-faint">CAS window</p>
          <div className="mt-1 flex gap-3">
            <Time label="Start" value={config.cas_window.start} disabled={disabled} onChange={(v) => onConfig({ cas_window: { ...config.cas_window, start: v } })} />
            <Time label="End" value={config.cas_window.end} disabled={disabled} onChange={(v) => onConfig({ cas_window: { ...config.cas_window, end: v } })} />
          </div>
          <p className="mt-1.5 text-hint text-faint">
            Both windows are entry windows. Between about 15:15 and 15:20 the signal has no
            reading (constituent books are empty), so spreads wait; after that it reads auction
            books, which its readiness evidence has not scored.
          </p>
        </div>
        <p className="text-hint text-faint">{CAS_BINGO_REGIME_NOTE}</p>
      </div>
    );
  }

  if (tab === "strategy") {
    return (
      <div className="space-y-3" role="radiogroup" aria-label="Strategy">
        <p className="text-hint text-faint">
          What Simulation and Autonomous deploy. The manual sheet always shows all five structures.
        </p>
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
        <p className="text-hint text-faint">Every structure buys its long leg first and sells only once it has filled.</p>
      </div>
    );
  }

  if (tab === "credit") {
    const patch = (p: Partial<CasBingoConfig["credit"]>) => onConfig({ credit: { ...c, ...p } });
    return (
      <div className="space-y-4">
        <Warning>
          <strong>Warning:</strong> {CAS_BINGO_CREDIT_WARNING}
        </Warning>
        <Num label="Margin to deploy" suffix="lakh" step={0.1} min={0.1} max={1000} value={c.margin_lakhs} disabled={disabled} onChange={(v) => patch({ margin_lakhs: v })} />
        <Num label="Move from open that arms it" suffix="%" step={0.05} min={0.05} max={10} value={c.move_trigger_pct} disabled={disabled} onChange={(v) => patch({ move_trigger_pct: v })} hint="The index must have risen (or dropped) this far from the day's open at the flip." />
        <Num label="Inner (sold) leg from open" suffix="%" step={0.05} min={0} max={20} value={c.inner_pct} disabled={disabled} onChange={(v) => patch({ inner_pct: v })} hint="Measured from the day's open, not spot — so after a big move the sold leg can be in the money." />
        <Num label="Outer (bought) leg from open" suffix="%" step={0.05} min={0.05} max={25} value={c.outer_pct} disabled={disabled} onChange={(v) => patch({ outer_pct: v })} />
        <Num label="Profit target" suffix="% of credit" min={1} max={100} value={c.target_pct} disabled={disabled} onChange={(v) => patch({ target_pct: v })} />
        <Num label="Stop-loss" suffix="% of credit" min={1} max={1000} value={c.stop_loss_pct} disabled={disabled} onChange={(v) => patch({ stop_loss_pct: v })} hint="If neither fires, the spread settles at expiry." />
      </div>
    );
  }

  if (tab === "debit") {
    const patch = (p: Partial<CasBingoConfig["debit"]>) => onConfig({ debit: { ...d, ...p } });
    return (
      <div className="space-y-4">
        <Num label="Net premium to pay" suffix="₹" step={500} min={1} max={10_000_000} value={d.premium_budget_inr} disabled={disabled} onChange={(v) => patch({ premium_budget_inr: v })} />
        <Num label="Strong signal" step={0.05} min={0.05} max={1} value={d.strong_threshold} disabled={disabled} onChange={(v) => patch({ strong_threshold: v })} hint="|signal| the flip must reach. The navbar's own entry is 0.30." />
        <Num label="Sustained for" suffix="min" step={0.5} min={0} max={60} value={d.sustain_minutes} disabled={disabled} onChange={(v) => patch({ sustain_minutes: v })} />
        <Num label="Inner (bought) leg from spot" suffix="%" step={0.05} min={0} max={20} value={d.inner_pct} disabled={disabled} onChange={(v) => patch({ inner_pct: v })} hint="0 means at the money. Measured from spot when it deploys." />
        <Num label="Outer (sold) leg from spot" suffix="%" step={0.05} min={0.05} max={25} value={d.outer_pct} disabled={disabled} onChange={(v) => patch({ outer_pct: v })} />
        <Num label="Profit target" suffix="% of debit" min={1} max={1000} value={d.target_pct} disabled={disabled} onChange={(v) => patch({ target_pct: v })} />
        <Num label="Stop-loss" suffix="% of debit" min={1} max={100} value={d.stop_loss_pct} disabled={disabled} onChange={(v) => patch({ stop_loss_pct: v })} />
      </div>
    );
  }

  if (tab === "strangle") {
    const patch = (p: Partial<CasBingoConfig["strangle"]>) => onConfig({ strangle: { ...s, ...p } });
    return (
      <div className="space-y-4">
        <Num label="Premium to pay" suffix="₹" step={500} min={1} max={10_000_000} value={s.premium_budget_inr} disabled={disabled} onChange={(v) => patch({ premium_budget_inr: v })} />
        <Time label="Entry time" value={s.entry_time_ist} disabled={disabled} onChange={(v) => patch({ entry_time_ist: v })} />
        <Num label="CE distance from spot" suffix="%" step={0.05} min={0} max={20} value={s.call_pct} disabled={disabled} onChange={(v) => patch({ call_pct: v })} />
        <Num label="PE distance from spot" suffix="%" step={0.05} min={0} max={20} value={s.put_pct} disabled={disabled} onChange={(v) => patch({ put_pct: v })} />
        <Num label="Profit target" suffix="% of debit" min={1} max={1000} value={s.target_pct} disabled={disabled} onChange={(v) => patch({ target_pct: v })} />
        <Num label="Stop-loss" suffix="% of debit" min={1} max={100} value={s.stop_loss_pct} disabled={disabled} onChange={(v) => patch({ stop_loss_pct: v })} />
      </div>
    );
  }

  if (tab === "liquidation") {
    const patch = (p: Partial<CasBingoConfig["liquidation"]>) => onConfig({ liquidation: { ...l, ...p } });
    return (
      <div className="space-y-4">
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
