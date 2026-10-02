"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { HelpLink } from "@/components/help/HelpLink";
import { SettingsScreenHeader } from "@/components/settings/SettingsScreenHeader";
import { AsyncLabelSpan } from "@/components/ui/AsyncLabelSpan";
import {
  getLiquiditySettings,
  putLiquiditySettings,
  type LiquiditySettings,
} from "@/lib/liquidity";

const QUERY_KEY = ["settings", "liquidity"] as const;

const INPUT_CLASS =
  "h-10 w-28 rounded-t-[3px] border-0 border-b border-muted bg-background dark:bg-elevated px-3 font-mono text-sm tabular-nums text-foreground outline-none transition hover:border-accent focus:border-accent-strong focus:bg-panel disabled:cursor-not-allowed disabled:opacity-60 [-moz-appearance:textfield] [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none";

function BookIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M4 7h10M4 12h7M4 17h4" />
      <path d="M20 7h-3M20 12h-6M20 17h-9" />
    </svg>
  );
}

function Field({
  id,
  label,
  unit,
  draft,
  onChange,
  disabled,
  min,
  max,
  step,
  help,
}: {
  id: string;
  label: string;
  unit: string;
  draft: string;
  onChange: (v: string) => void;
  disabled: boolean;
  min: number;
  max: number;
  step: number;
  help: string;
}) {
  return (
    <div>
      <label htmlFor={id} className="mb-1.5 block text-micro font-semibold uppercase tracking-[.06em] text-faint">
        {label}
      </label>
      <div className="flex flex-wrap items-center gap-2">
        <input
          id={id}
          type="number"
          min={min}
          max={max}
          step={step}
          inputMode="decimal"
          className={INPUT_CLASS}
          value={draft}
          onChange={(e) => onChange(e.target.value)}
          disabled={disabled}
        />
        <span className="text-xs text-muted">{unit}</span>
      </div>
      <p className="mt-1.5 text-table text-muted">{help}</p>
    </div>
  );
}

/**
 * Settings → Liquidity Checks (docs/liquidity-checks-plan.md). The thresholds the order tickets,
 * the Strategy Builder and the bots all judge a live order book by. Global, read fresh on every
 * check, so a change applies to the next one.
 */
export function LiquidityChecksScreen() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: QUERY_KEY, queryFn: getLiquiditySettings });
  const [pct, setPct] = useState("");
  const [ticks, setTicks] = useState("");
  const [staleMin, setStaleMin] = useState("");

  useEffect(() => {
    if (!q.data) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- syncs local drafts from server values once loaded
    setPct(String(q.data.max_deviation_pct));
    setTicks(String(q.data.min_deviation_ticks));
    setStaleMin(String(Math.round((q.data.ltp_stale_seconds / 60) * 10) / 10));
  }, [q.data]);

  const save = useMutation({
    mutationFn: putLiquiditySettings,
    onSuccess: (data: LiquiditySettings) => qc.setQueryData(QUERY_KEY, data),
  });

  const b = q.data?.bounds;
  const disabled = q.isLoading || save.isPending;

  return (
    <div>
      <SettingsScreenHeader
        icon={<BookIcon />}
        title="Liquidity Checks"
        description="When a quantity is too large for the live order book to fill near the LTP."
      />
      <div className="space-y-4">
        <p className="text-xs leading-relaxed text-muted">
          Place Order, Basket Order and the Strategy Builder show a ⚠ beside a quantity whose estimated
          average fill, walked through the live order book, is further from the LTP than <strong>both</strong>{" "}
          limits below. The Strategy Builder caps its proposals to what the book absorbs, and bots trade a
          smaller size or skip the entry. Exits are never held back.{" "}
          <HelpLink topicId="liquidity-checks" className="text-xs">
            How the check works
          </HelpLink>
        </p>

        {q.data?.depth.capped ? (
          <div className="rounded-[8px] border border-amber-accent/40 bg-amber-tint px-3 py-2.5 text-xs leading-relaxed text-amber-on-tint">
            ICICI refused some order-book subscriptions today, so the full book is kept only for strikes near
            the money and for the legs you are entering. Other strikes are checked against the best bid and
            offer only.
          </div>
        ) : null}

        {q.error ? (
          <p className="text-xs text-down">
            {q.error instanceof Error ? q.error.message : "Could not load liquidity settings"}
          </p>
        ) : null}

        <section className="app-card space-y-4 p-5">
          <Field
            id="liquidity-max-deviation-pct"
            label="Distance from LTP"
            unit="% of LTP"
            draft={pct}
            onChange={setPct}
            disabled={disabled}
            min={b?.max_deviation_pct_min ?? 0.5}
            max={b?.max_deviation_pct_max ?? 100}
            step={0.5}
            help="Warn when the estimated average fill is further than this from the LTP."
          />
          <Field
            id="liquidity-min-deviation-ticks"
            label="And at least"
            unit="ticks (₹0.05 each)"
            draft={ticks}
            onChange={setTicks}
            disabled={disabled}
            min={b?.min_deviation_ticks_min ?? 0}
            max={b?.min_deviation_ticks_max ?? 1000}
            step={1}
            help="Keeps cheap options quiet: on a ₹0.80 option a single tick is already 6%."
          />
          <Field
            id="liquidity-ltp-stale-minutes"
            label="LTP is old after"
            unit="minutes"
            draft={staleMin}
            onChange={setStaleMin}
            disabled={disabled}
            min={(b?.ltp_stale_seconds_min ?? 10) / 60}
            max={(b?.ltp_stale_seconds_max ?? 3600) / 60}
            step={0.5}
            help="A last trade older than this, or an LTP outside the current bid and ask, gets its own warning: the fill will be near the book, not the LTP."
          />
        </section>

        <div className="flex items-center gap-2">
          <button
            type="button"
            className="app-btn-outline rounded-[9px] px-4 py-2 text-xs"
            disabled={disabled || !pct.trim() || !ticks.trim() || !staleMin.trim()}
            aria-busy={save.isPending}
            onClick={() => {
              const body = {
                max_deviation_pct: parseFloat(pct),
                min_deviation_ticks: Math.round(parseFloat(ticks)),
                ltp_stale_seconds: Math.round(parseFloat(staleMin) * 60),
              };
              if (Object.values(body).some((v) => !Number.isFinite(v))) {
                alert("Enter a number in each field.");
                return;
              }
              save.mutate(body, {
                onError: (e) => alert(e instanceof Error ? e.message : "Save failed"),
                onSuccess: () => alert("Saved. The next check uses these limits."),
              });
            }}
          >
            <AsyncLabelSpan busy={save.isPending} idleLabel="Save" busyLabel="Saving…" />
          </button>
        </div>
      </div>
    </div>
  );
}
