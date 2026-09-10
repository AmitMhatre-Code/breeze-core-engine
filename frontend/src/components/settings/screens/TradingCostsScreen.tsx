"use client";

import { useState } from "react";
import { SettingsScreenHeader } from "@/components/settings/SettingsScreenHeader";
import { NumberInput } from "@/components/ui/NumberInput";
import {
  useTradingCharges,
  useUpdateTradingCharges,
  type TradingCharges,
} from "@/lib/use-bots";

function CoinIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <ellipse cx="12" cy="6" rx="8" ry="3" />
      <path d="M4 6v6c0 1.7 3.6 3 8 3s8-1.3 8-3V6" />
      <path d="M4 12v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6" />
    </svg>
  );
}

type FieldSpec = {
  key: keyof TradingCharges;
  label: string;
  suffix: string;
  step: number;
  max: number;
  hint?: string;
};

const BROKERAGE: FieldSpec[] = [
  { key: "brokerage_per_order_inr", label: "Brokerage per order", suffix: "₹", step: 1, max: 10000,
    hint: "Charged once per order, so a leg chunked under the freeze limit pays it more than once." },
  { key: "brokerage_pct_of_premium", label: "Brokerage % of premium", suffix: "%", step: 0.01, max: 10,
    hint: "Zero on a flat plan." },
];

const STATUTORY: FieldSpec[] = [
  { key: "stt_sell_pct", label: "STT", suffix: "%", step: 0.001, max: 5,
    hint: "Sell side only." },
  { key: "exchange_txn_pct", label: "Exchange transaction — NSE", suffix: "%", step: 0.0001, max: 5,
    hint: "Includes IPFT, which the contract note does not bill separately." },
  { key: "exchange_txn_pct_bse", label: "Exchange transaction — BSE", suffix: "%", step: 0.0001, max: 5,
    hint: "BSE genuinely differs from NSE." },
  { key: "sebi_pct", label: "SEBI turnover", suffix: "%", step: 0.0001, max: 5,
    hint: "₹10 per crore." },
  { key: "ipft_pct", label: "IPFT (if billed separately)", suffix: "%", step: 0.0001, max: 5,
    hint: "Leave at zero unless your contract note shows it as its own line — otherwise it is counted twice." },
  { key: "stamp_buy_pct", label: "Stamp duty", suffix: "%", step: 0.0001, max: 5,
    hint: "Buy side only, levied on the day's aggregate buy value." },
  { key: "gst_pct", label: "GST", suffix: "%", step: 0.1, max: 100,
    hint: "On brokerage and exchange fees — not on STT or stamp duty, which are taxes rather than services." },
];

const SIMULATION: FieldSpec[] = [
  { key: "slippage_spread_fraction", label: "Simulation slippage", suffix: "× spread", step: 0.05, max: 2,
    hint: "Adverse on each leg. Simulation mode and the backtest fill at the touch, which is optimistic; this is the correction. Does not affect live orders." },
];

function Group({
  title,
  fields,
  draft,
  onChange,
  disabled,
}: {
  title: string;
  fields: FieldSpec[];
  draft: TradingCharges;
  onChange: (patch: Partial<TradingCharges>) => void;
  disabled: boolean;
}) {
  return (
    <section className="mt-6">
      <h3 className="text-micro font-semibold uppercase tracking-[0.06em] text-faint">{title}</h3>
      <div className="mt-3 grid gap-4 sm:grid-cols-2">
        {fields.map((f) => (
          <label key={String(f.key)} className="block">
            <span className="block text-body text-text">{f.label}</span>
            <div className="mt-1 flex items-center gap-2">
              <NumberInput
                min={0}
                max={f.max}
                step={f.step}
                value={Number(draft[f.key] ?? 0)}
                disabled={disabled}
                aria-label={f.label}
                onChange={(v) => onChange({ [f.key]: v } as Partial<TradingCharges>)}
                className="app-input w-32 font-mono tabular-nums"
              />
              <span className="text-hint text-faint">{f.suffix}</span>
            </div>
            {f.hint && <span className="mt-1 block text-hint text-faint">{f.hint}</span>}
          </label>
        ))}
      </div>
    </section>
  );
}

/** One round trip on one NIFTY lot, so the effect of an edit is visible immediately rather
 *  than only after a session's worth of trades. */
function roundTripPreview(c: TradingCharges): number {
  const qty = 75;
  const leg = (price: number, isBuy: boolean) => {
    const turnover = price * qty;
    const brokerage = c.brokerage_per_order_inr + (c.brokerage_pct_of_premium / 100) * turnover;
    const capped = c.brokerage_cap_inr ? Math.min(brokerage, c.brokerage_cap_inr) : brokerage;
    const exch = (turnover * c.exchange_txn_pct) / 100;
    const sebi = (turnover * c.sebi_pct) / 100;
    const ipft = (turnover * c.ipft_pct) / 100;
    const stt = isBuy ? 0 : (turnover * c.stt_sell_pct) / 100;
    const stamp = isBuy ? (turnover * c.stamp_buy_pct) / 100 : 0;
    const gst = ((capped + exch + sebi + ipft) * c.gst_pct) / 100;
    return capped + exch + sebi + ipft + stt + stamp + gst;
  };
  return leg(110, true) + leg(120, false);
}

export function TradingCostsScreen() {
  const { data, isLoading, isError, error } = useTradingCharges();
  const update = useUpdateTradingCharges();
  // Only the edits are held; the server response is the base they are laid over, so a
  // refetch cannot silently discard an in-flight change.
  const [edits, setEdits] = useState<Partial<TradingCharges>>({});

  if (isLoading) return <p className="app-text-muted text-body">Loading cost model…</p>;
  if (isError || !data) {
    return (
      <p className="text-body text-down">
        Could not load trading costs: {(error as Error)?.message ?? "unknown error"}
      </p>
    );
  }

  const draft: TradingCharges = { ...data, ...edits };
  const dirty = Object.keys(edits).some(
    (k) => draft[k as keyof TradingCharges] !== data[k as keyof TradingCharges],
  );
  const set = (patch: Partial<TradingCharges>) => setEdits((e) => ({ ...e, ...patch }));

  return (
    <div>
      <SettingsScreenHeader
        icon={<CoinIcon />}
        title="Trading Costs"
        description="Brokerage and statutory charges, used by every bot and by the backtest."
      />

      <p className="app-card-muted mt-4 p-3 text-hint">
        These are used in <b>one place for all bots</b> — the scalpers price every round trip
        against them, the writers show a net-of-charges premium on their proposals, and the
        backtest deducts them from every simulated cycle. No bot has its own copy, so they can
        never disagree about what a trade costs.
      </p>
      <p className="app-card-muted mt-2 p-3 text-hint">
        ⚠️ Calibrated against a real ICICI contract note, but rates are set by regulation and
        <b> change without notice</b>. Check them against a recent contract note before relying
        on them. Per-trade charges are itemised in the bots&apos; run log, so a mismatch shows
        up as a number that disagrees with your broker rather than a silent bias.
      </p>

      <Group title="Brokerage" fields={BROKERAGE} draft={draft} onChange={set} disabled={update.isPending} />
      <Group title="Statutory charges" fields={STATUTORY} draft={draft} onChange={set} disabled={update.isPending} />
      <Group title="Simulation only" fields={SIMULATION} draft={draft} onChange={set} disabled={update.isPending} />

      <p className="app-text-muted mt-6 text-body">
        One round trip on a single NIFTY lot (75) at ₹110 → ₹120:{" "}
        <b className="font-mono tabular-nums text-text">₹{roundTripPreview(draft).toFixed(2)}</b>
      </p>

      <div className="mt-4 flex items-center gap-3">
        <button
          type="button"
          className="app-btn-primary"
          disabled={!dirty || update.isPending}
          onClick={() =>
            void update
              .mutateAsync(draft)
              .then(() => setEdits({}))
              .catch(() => undefined)
          }
        >
          {update.isPending ? "Saving…" : "Save"}
        </button>
        <button
          type="button"
          className="app-btn-secondary"
          disabled={!dirty || update.isPending}
          onClick={() => setEdits({})}
        >
          Discard
        </button>
        {update.isError && (
          <span className="text-hint text-down">
            {(update.error as Error)?.message ?? "Could not save."}
          </span>
        )}
      </div>
    </div>
  );
}
