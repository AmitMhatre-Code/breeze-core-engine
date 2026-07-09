"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { AsyncLabelSpan } from "@/components/ui/AsyncLabelSpan";
import { SettingsScreenHeader } from "@/components/settings/SettingsScreenHeader";
import { apiClient } from "@/lib/api-client";

type PnlEnginePreferences = {
  quote_flush_interval_seconds: number;
  pnl_recompute_interval_seconds: number;
  quote_flush_min_seconds: number;
  quote_flush_max_seconds: number;
  quote_flush_recommended_min_seconds: number;
  quote_flush_recommended_max_seconds: number;
  pnl_recompute_min_seconds: number;
  pnl_recompute_max_seconds: number;
  pnl_recompute_recommended_min_seconds: number;
  pnl_recompute_recommended_max_seconds: number;
};

type Zone = "ok" | "caution" | "danger" | "lagging";

function zoneFor(value: number, hardMin: number, recMin: number, recMax: number): Zone {
  if (!Number.isFinite(value)) return "caution";
  if (value < recMin) {
    const dangerCutoff = hardMin + (recMin - hardMin) * 0.34;
    return value <= dangerCutoff ? "danger" : "caution";
  }
  if (value > recMax) return "lagging";
  return "ok";
}

const ZONE_DOT_CLASS: Record<Zone, string> = {
  ok: "bg-up",
  caution: "bg-amber-accent",
  danger: "bg-down",
  lagging: "bg-amber-accent",
};

const ZONE_TEXT_CLASS: Record<Zone, string> = {
  ok: "text-up",
  caution: "text-amber-accent",
  danger: "text-down",
  lagging: "text-amber-accent",
};

function GaugeIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M12 14v-3" />
      <path d="M4.6 19a9 9 0 1 1 14.8 0" />
      <path d="M12 5v0" />
    </svg>
  );
}

function IntervalField({
  id,
  label,
  draft,
  onChange,
  disabled,
  min,
  max,
  recommendedMin,
  recommendedMax,
  fastRiskCopy,
  slowRiskCopy,
}: {
  id: string;
  label: string;
  draft: string;
  onChange: (v: string) => void;
  disabled: boolean;
  min: number;
  max: number;
  recommendedMin: number;
  recommendedMax: number;
  fastRiskCopy: string;
  slowRiskCopy: string;
}) {
  const parsed = parseFloat(draft);
  const zone = zoneFor(parsed, min, recommendedMin, recommendedMax);

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
          step={0.1}
          inputMode="decimal"
          className="h-10 w-28 rounded-t-[3px] border-0 border-b border-muted bg-background dark:bg-elevated px-3 font-mono text-sm tabular-nums text-foreground outline-none transition hover:border-accent focus:border-accent-strong focus:bg-panel disabled:cursor-not-allowed disabled:opacity-60 [-moz-appearance:textfield] [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none"
          value={draft}
          onChange={(e) => onChange(e.target.value)}
          disabled={disabled}
        />
        <span className="text-xs text-muted">seconds</span>
        <span className="inline-flex items-center gap-1.5 text-hint text-muted">
          <span className={`size-1.5 rounded-full ${ZONE_DOT_CLASS[zone]}`} aria-hidden />
          Recommended: {recommendedMin}–{recommendedMax}s
        </span>
      </div>
      {zone === "danger" ? (
        <p className={`mt-1.5 text-table font-medium ${ZONE_TEXT_CLASS.danger}`}>
          ⚠ Very aggressive — {fastRiskCopy}
        </p>
      ) : zone === "caution" ? (
        <p className={`mt-1.5 text-table ${ZONE_TEXT_CLASS.caution}`}>
          Below the recommended minimum — {fastRiskCopy}
        </p>
      ) : zone === "lagging" ? (
        <p className={`mt-1.5 text-table ${ZONE_TEXT_CLASS.lagging}`}>
          Above the recommended maximum — {slowRiskCopy}
        </p>
      ) : null}
    </div>
  );
}

export function AdvancedPnlEngineScreen() {
  const qc = useQueryClient();
  const [flushDraft, setFlushDraft] = useState("");
  const [recomputeDraft, setRecomputeDraft] = useState("");

  const q = useQuery({
    queryKey: ["settings", "pnl-engine-preferences"],
    queryFn: () => apiClient.get<PnlEnginePreferences>("/api/settings/pnl-engine/preferences"),
  });

  useEffect(() => {
    if (q.data) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- syncs local drafts from server values once loaded
      setFlushDraft(String(q.data.quote_flush_interval_seconds));
      setRecomputeDraft(String(q.data.pnl_recompute_interval_seconds));
    }
  }, [q.data?.quote_flush_interval_seconds, q.data?.pnl_recompute_interval_seconds]);

  const save = useMutation({
    mutationFn: (body: { quote_flush_interval_seconds: number; pnl_recompute_interval_seconds: number }) =>
      apiClient.put<PnlEnginePreferences>("/api/settings/pnl-engine/preferences", body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["settings", "pnl-engine-preferences"] });
    },
  });

  const data = q.data;
  const disabled = q.isLoading || save.isPending;

  return (
    <div>
      <SettingsScreenHeader
        icon={<GaugeIcon />}
        title="Advanced Engine Settings"
        description="How often live quotes are cached and portfolio P&L / exit triggers are recalculated."
      />

      <div className="space-y-4">
        <div className="rounded-[8px] border border-amber-accent/40 bg-amber-tint px-3 py-2.5 text-xs leading-relaxed text-amber-accent">
          <strong className="font-semibold">Read before changing these.</strong> This app runs on a small,
          burst-credit-limited server. Lower values recompute more often — more responsive exit triggers, but
          more CPU and Redis load per second. Set either value too low and you risk exhausting the
          instance&rsquo;s CPU credits, which can make the entire app (including order placement and the live WebSocket feed)
          slow, unresponsive, or crash outright. Set them too high and target/stop-loss exits react slower to
          price moves. Changes apply to the running app within one cycle — no restart needed.
        </div>

        {q.error ? (
          <p className="text-xs text-down">
            {q.error instanceof Error ? q.error.message : "Could not load advanced settings"}
          </p>
        ) : null}

        <section className="app-card space-y-3 p-5">
          <h3 className="text-heading font-bold text-foreground">WS quote flush interval</h3>
          <p className="text-xs leading-relaxed text-muted">
            How often the latest LTP/bid/ask conflated from live WebSocket ticks is written to Redis as a
            batch. This is the data source the P&L recompute below reads from — it never calls the broker
            directly.
          </p>
          <IntervalField
            id="quote-flush-interval"
            label="Seconds between flushes"
            draft={flushDraft}
            onChange={setFlushDraft}
            disabled={disabled}
            min={data?.quote_flush_min_seconds ?? 0.5}
            max={data?.quote_flush_max_seconds ?? 10}
            recommendedMin={data?.quote_flush_recommended_min_seconds ?? 1.5}
            recommendedMax={data?.quote_flush_recommended_max_seconds ?? 2}
            fastRiskCopy="frequent Redis writes at this rate can strain a small instance under heavy tick volume."
            slowRiskCopy="the P&L engine below will be pricing legs against a staler quote."
          />
        </section>

        <section className="app-card space-y-3 p-5">
          <h3 className="text-heading font-bold text-foreground">P&L recompute interval</h3>
          <p className="text-xs leading-relaxed text-muted">
            How often every open leg is repriced and checked against target/stop-loss exit rules. Runs
            entirely off the cached quotes above — never a broker API call, so it never counts against the
            ICICI rate limit.
          </p>
          <IntervalField
            id="pnl-recompute-interval"
            label="Seconds between recomputes"
            draft={recomputeDraft}
            onChange={setRecomputeDraft}
            disabled={disabled}
            min={data?.pnl_recompute_min_seconds ?? 1}
            max={data?.pnl_recompute_max_seconds ?? 30}
            recommendedMin={data?.pnl_recompute_recommended_min_seconds ?? 2}
            recommendedMax={data?.pnl_recompute_recommended_max_seconds ?? 5}
            fastRiskCopy="recomputing this often across many open legs adds sustained CPU load."
            slowRiskCopy="a strategy's exit trigger may fire noticeably later than the price move that caused it."
          />
        </section>

        <div className="flex items-center gap-2">
          <button
            type="button"
            className="app-btn-outline rounded-[9px] px-4 py-2 text-xs"
            disabled={disabled || flushDraft.trim() === "" || recomputeDraft.trim() === ""}
            aria-busy={save.isPending}
            onClick={() => {
              const flush = parseFloat(flushDraft.trim());
              const recompute = parseFloat(recomputeDraft.trim());
              const flushMin = data?.quote_flush_min_seconds ?? 0.5;
              const flushMax = data?.quote_flush_max_seconds ?? 10;
              const recomputeMin = data?.pnl_recompute_min_seconds ?? 1;
              const recomputeMax = data?.pnl_recompute_max_seconds ?? 30;
              if (!Number.isFinite(flush) || flush < flushMin || flush > flushMax) {
                alert(`Quote flush interval must be between ${flushMin} and ${flushMax} seconds.`);
                return;
              }
              if (!Number.isFinite(recompute) || recompute < recomputeMin || recompute > recomputeMax) {
                alert(`P&L recompute interval must be between ${recomputeMin} and ${recomputeMax} seconds.`);
                return;
              }
              save.mutate(
                { quote_flush_interval_seconds: flush, pnl_recompute_interval_seconds: recompute },
                {
                  onError: (e) => alert(e instanceof Error ? e.message : "Save failed"),
                  onSuccess: () => alert("Saved. The running app will pick this up within one cycle."),
                },
              );
            }}
          >
            <AsyncLabelSpan busy={save.isPending} idleLabel="Save" busyLabel="Saving…" />
          </button>
        </div>
      </div>
    </div>
  );
}
