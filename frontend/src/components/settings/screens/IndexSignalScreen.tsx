"use client";

import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { AsyncLabelSpan } from "@/components/ui/AsyncLabelSpan";
import { SettingsScreenHeader } from "@/components/settings/SettingsScreenHeader";
import { fetchMarketStatus } from "@/lib/market-status";
import { useIndexQuotes } from "@/lib/use-index-quotes";
import {
  downloadIndexSignalReadings,
  fetchIndexSignalPreferences,
  fetchIndexSignalReadiness,
  fetchIndexSignalShadowReport,
  fetchIndexSignalWeights,
  INDEX_SIGNAL_PREFERENCES_QUERY_KEY,
  INDEX_SIGNAL_READINESS_QUERY_KEY,
  INDEX_SIGNAL_SHADOW_REPORT_QUERY_KEY,
  INDEX_SIGNAL_WEIGHTS_QUERY_KEY,
  refreshIndexSignalWeights,
  saveIndexSignalPreferences,
  WEIGHTS_SOURCE_LABEL,
  type CallStatus,
  type IndexLabel,
  type IndexReadiness,
  type ReadinessCall,
  type ReadinessStatus,
  type IndexSignalFieldBound,
  type IndexSignalNumericField,
  type IndexSignalPreferences,
  type IndexWeightsOverview,
  type ShadowBucket,
  type ShadowExcluded,
  type ShadowReport,
} from "@/lib/settings/index-signal";

const INDICES: { key: IndexLabel; name: string; books: string }[] = [
  { key: "nifty", name: "NIFTY 50", books: "NSE order books" },
  { key: "sensex", name: "SENSEX", books: "BSE order books" },
];

type FieldSpec = {
  key: IndexSignalNumericField;
  label: string;
  unit: string;
  step: number;
  help: string;
  belowCopy: string;
  aboveCopy: string;
};

const SIGNAL_FIELDS: FieldSpec[] = [
  {
    key: "enter_threshold",
    label: "Enter threshold",
    unit: "imbalance",
    step: 0.01,
    help: "The smoothed order-book imbalance (−1 to +1) must pass ±this for the signal to turn bullish or bearish.",
    belowCopy: "the signal will take sides on ordinary order-book noise.",
    aboveCopy: "only rare, extreme imbalances will register.",
  },
  {
    key: "exit_threshold",
    label: "Exit threshold",
    unit: "imbalance",
    step: 0.01,
    help: "Once on a side, the signal holds it until the imbalance falls back inside ±this. Must not exceed the enter threshold.",
    belowCopy: "a side will be held long after the pressure has faded.",
    aboveCopy: "the signal will flicker between a side and neutral.",
  },
  {
    key: "tau_seconds",
    label: "Smoothing time constant (τ)",
    unit: "seconds",
    step: 0.5,
    help: "How quickly the signal follows the order books. Changing it restarts the smoothing, with a warm-up of 2τ.",
    belowCopy: "short-lived quotes placed to mislead (spoofing) pass straight through.",
    aboveCopy: "the signal will react late to a genuine shift.",
  },
];

const FEED_FIELDS: FieldSpec[] = [
  {
    key: "top_n",
    label: "Tracked stocks per index",
    unit: "stocks",
    step: 1,
    help: "The heaviest stocks by index weight. Each is one live order-book subscription: on NSE for NIFTY, on BSE for SENSEX.",
    belowCopy: "the signal rests on too little of the index.",
    aboveCopy: "more subscriptions for very little added index weight.",
  },
  {
    key: "min_coverage",
    label: "Minimum coverage",
    unit: "share of tracked weight",
    step: 0.05,
    help: "How much of the tracked weight needs a live order book. Below it the signal shows as unavailable — never as neutral.",
    belowCopy: "a handful of stocks can speak for the whole index.",
    aboveCopy: "a single quiet order book will blank the signal.",
  },
  {
    key: "book_stale_seconds",
    label: "Order-book staleness",
    unit: "seconds",
    step: 1,
    help: "A stock whose order book hasn't updated for this long is left out until it updates again.",
    belowCopy: "thinner BSE order books will keep dropping out between updates.",
    aboveCopy: "an order book keeps counting long after it stopped updating.",
  },
  {
    key: "depth_levels",
    label: "Depth levels per side",
    unit: "levels",
    step: 1,
    help: "How many of the best bid and ask price levels are added up. Both exchanges send five.",
    belowCopy: "only the top of the order book counts, which is noisier.",
    aboveCopy: "",
  },
];

const EVIDENCE_FIELDS: FieldSpec[] = [
  {
    key: "shadow_retention_days",
    label: "Evidence retention",
    unit: "days",
    step: 1,
    help: "How long the per-minute log behind the shadow evidence below is kept.",
    belowCopy: "less history to judge the signal on.",
    aboveCopy: "more rows kept in the database for little added insight.",
  },
];

const ALL_FIELDS: FieldSpec[] = [...SIGNAL_FIELDS, ...FEED_FIELDS, ...EVIDENCE_FIELDS];

type Drafts = Partial<Record<IndexSignalNumericField, string>>;
type Issue = { tone: "error" | "warn"; text: string } | null;

function recommendedRange(b: IndexSignalFieldBound): string {
  return b.recommended_min === b.recommended_max
    ? `${b.recommended_min}`
    : `${b.recommended_min}–${b.recommended_max}`;
}

function fieldIssue(spec: FieldSpec, bound: IndexSignalFieldBound, draft: string): Issue {
  const value = Number(draft);
  if (draft.trim() === "" || !Number.isFinite(value) || value < bound.min || value > bound.max) {
    return { tone: "error", text: `Must be between ${bound.min} and ${bound.max}.` };
  }
  if (bound.integer && !Number.isInteger(value)) {
    return { tone: "error", text: "Must be a whole number." };
  }
  if (value < bound.recommended_min && spec.belowCopy) {
    return { tone: "warn", text: `Below the recommended range — ${spec.belowCopy}` };
  }
  if (value > bound.recommended_max && spec.aboveCopy) {
    return { tone: "warn", text: `Above the recommended range — ${spec.aboveCopy}` };
  }
  return null;
}

function SignalIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M3 17l5-5 4 4 8-8" />
      <path d="M15 8h5v5" />
    </svg>
  );
}

function Switch({
  enabled,
  onChange,
  disabled,
  label,
}: {
  enabled: boolean;
  onChange: (next: boolean) => void;
  disabled: boolean;
  label: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={enabled}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!enabled)}
      className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition disabled:cursor-not-allowed disabled:opacity-45 ${
        enabled ? "bg-accent-strong" : "bg-border"
      }`}
    >
      <span
        className={`inline-block size-5 transform rounded-full bg-white shadow transition ${
          enabled ? "translate-x-[22px]" : "translate-x-0.5"
        }`}
      />
    </button>
  );
}

function NumberField({
  spec,
  bound,
  draft,
  onChange,
  disabled,
  issue,
}: {
  spec: FieldSpec;
  bound: IndexSignalFieldBound;
  draft: string;
  onChange: (v: string) => void;
  disabled: boolean;
  issue: Issue;
}) {
  const id = `index-signal-${spec.key}`;
  const dot = issue == null ? "bg-up" : issue.tone === "error" ? "bg-down" : "bg-amber-accent";
  return (
    <div>
      <label htmlFor={id} className="mb-1.5 block text-micro font-semibold uppercase tracking-[.06em] text-faint">
        {spec.label}
      </label>
      <div className="flex flex-wrap items-center gap-2">
        <input
          id={id}
          type="number"
          min={bound.min}
          max={bound.max}
          step={spec.step}
          inputMode="decimal"
          className="h-10 w-28 rounded-t-[3px] border-0 border-b border-muted bg-background dark:bg-elevated px-3 font-mono text-sm tabular-nums text-foreground outline-none transition hover:border-accent focus:border-accent-strong focus:bg-panel disabled:cursor-not-allowed disabled:opacity-60 [-moz-appearance:textfield] [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none"
          value={draft}
          onChange={(e) => onChange(e.target.value)}
          disabled={disabled}
          aria-invalid={issue?.tone === "error"}
        />
        <span className="text-xs text-muted">{spec.unit}</span>
        <span className="inline-flex items-center gap-1.5 text-hint text-muted">
          <span className={`size-1.5 rounded-full ${dot}`} aria-hidden />
          Recommended: {recommendedRange(bound)}
        </span>
      </div>
      <p className="mt-1.5 text-table leading-relaxed text-muted">{spec.help}</p>
      {issue ? (
        <p className={`mt-1 text-table ${issue.tone === "error" ? "font-medium text-down" : "text-amber-accent"}`}>
          {issue.text}
        </p>
      ) : null}
    </div>
  );
}

export function IndexSignalScreen() {
  return (
    <div>
      <SettingsScreenHeader
        icon={<SignalIcon />}
        title="Index Signal"
        description="NIFTY and SENSEX bullish / bearish reading from the heaviest stocks' live order books. It is the one signal the navbar, the screens and the bots all read."
      />
      <div className="space-y-4">
        <div className="rounded-[8px] border border-border-soft bg-panel2 px-3 py-2.5 text-xs leading-relaxed text-muted">
          <strong className="font-semibold text-foreground">Shadow mode.</strong>{" "}
          The signal is shown in the navbar
          and logged alongside what the index did next, but no bot acts on it yet. Review the shadow evidence at the
          bottom of this page before letting bots trade on it. Changes here reach the running app within one P&amp;L
          recompute cycle — no restart needed.
        </div>
        <PreferencesSection />
        <WeightsSection />
        <ShadowEvidenceSection />
      </div>
    </div>
  );
}

function PreferencesSection() {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: INDEX_SIGNAL_PREFERENCES_QUERY_KEY,
    queryFn: fetchIndexSignalPreferences,
  });
  // Drafts only hold what the user has touched; everything else reads straight from the server
  // value, so a save (which clears the drafts) shows exactly what the backend stored.
  const [drafts, setDrafts] = useState<Drafts>({});
  const [notice, setNotice] = useState<{ tone: "ok" | "error"; text: string } | null>(null);
  const [switchNotice, setSwitchNotice] = useState<{ tone: "ok" | "error"; text: string } | null>(null);

  // The switch saves on its own the moment it is flipped. It leaves `drafts` alone, so unsaved
  // edits to the numeric fields survive a flip.
  const toggle = useMutation({
    mutationFn: (next: boolean) => saveIndexSignalPreferences({ enabled: next }),
    onSuccess: (updated) => {
      qc.setQueryData<IndexSignalPreferences>(INDEX_SIGNAL_PREFERENCES_QUERY_KEY, updated);
      setSwitchNotice({
        tone: "ok",
        text: updated.enabled
          ? "Switched on. The signal starts publishing within one cycle."
          : "Switched off. The navbar chip disappears within one cycle.",
      });
      void qc.invalidateQueries({ queryKey: ["dashboard", "index-quotes"] });
    },
    onError: (e) =>
      setSwitchNotice({ tone: "error", text: e instanceof Error ? e.message : "Could not switch the signal" }),
  });

  const save = useMutation({
    mutationFn: saveIndexSignalPreferences,
    onSuccess: (updated) => {
      qc.setQueryData<IndexSignalPreferences>(INDEX_SIGNAL_PREFERENCES_QUERY_KEY, updated);
      setDrafts({});
      setNotice({ tone: "ok", text: "Saved. The running app applies this within one cycle." });
      // The navbar chip's tooltip shows the thresholds, and a new stock count changes the weights table.
      void qc.invalidateQueries({ queryKey: ["dashboard", "index-quotes"] });
      void qc.invalidateQueries({ queryKey: INDEX_SIGNAL_WEIGHTS_QUERY_KEY });
    },
    onError: (e) => setNotice({ tone: "error", text: e instanceof Error ? e.message : "Save failed" }),
  });

  const data = q.data;
  const disabled = !data || save.isPending;
  // Until the stored value arrives the switch shows off (and is disabled) rather than guessing
  // "on" — a wrong guess read as the signal's real state. While a flip saves, it shows the
  // position asked for.
  const enabled = (toggle.isPending ? toggle.variables : undefined) ?? data?.enabled ?? false;
  const valueOf = (key: IndexSignalNumericField): string =>
    drafts[key] ?? (data ? String(data[key]) : "");

  const issues = new Map<IndexSignalNumericField, Issue>();
  if (data) {
    for (const spec of ALL_FIELDS) issues.set(spec.key, fieldIssue(spec, data.bounds[spec.key], valueOf(spec.key)));
    const enter = Number(valueOf("enter_threshold"));
    const exit = Number(valueOf("exit_threshold"));
    if (issues.get("exit_threshold")?.tone !== "error" && Number.isFinite(enter) && exit > enter) {
      issues.set("exit_threshold", { tone: "error", text: "Must not exceed the enter threshold." });
    }
  }
  const hasError = [...issues.values()].some((i) => i?.tone === "error");
  const dirty = data != null && ALL_FIELDS.some((spec) => valueOf(spec.key) !== String(data[spec.key]));

  const renderFields = (specs: FieldSpec[]) =>
    data ? (
      <div className="grid gap-5 md:grid-cols-2">
        {specs.map((spec) => (
          <NumberField
            key={spec.key}
            spec={spec}
            bound={data.bounds[spec.key]}
            draft={valueOf(spec.key)}
            onChange={(v) => {
              setNotice(null);
              setDrafts((prev) => ({ ...prev, [spec.key]: v }));
            }}
            disabled={disabled}
            issue={issues.get(spec.key) ?? null}
          />
        ))}
      </div>
    ) : null;

  return (
    <>
      {q.error ? (
        <p className="text-xs text-down">
          {q.error instanceof Error ? q.error.message : "Could not load the index signal settings"}
        </p>
      ) : null}

      <section className="app-card space-y-3 p-5">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h3 className="text-heading font-bold text-foreground">Signal</h3>
            <p className="mt-1 text-xs leading-relaxed text-muted">
              When off, the order-book subscriptions are dropped, nothing is published or logged, and the navbar
              chip disappears. Anything that reads the signal sees it as unavailable. The switch saves as soon as
              you flip it.
            </p>
            {switchNotice ? (
              <p className={`mt-1.5 text-xs ${switchNotice.tone === "ok" ? "text-up" : "text-down"}`}>
                {switchNotice.text}
              </p>
            ) : null}
          </div>
          <Switch
            enabled={enabled}
            onChange={(next) => {
              setSwitchNotice(null);
              toggle.mutate(next);
            }}
            disabled={!data || toggle.isPending}
            label="Index signal enabled"
          />
        </div>
      </section>

      <section className="app-card space-y-4 p-5">
        <h3 className="text-heading font-bold text-foreground">Smoothing &amp; thresholds</h3>
        {renderFields(SIGNAL_FIELDS)}
      </section>

      <section className="app-card space-y-4 p-5">
        <h3 className="text-heading font-bold text-foreground">Order-book feed</h3>
        {renderFields(FEED_FIELDS)}
      </section>

      <section className="app-card space-y-4 p-5">
        <h3 className="text-heading font-bold text-foreground">Evidence</h3>
        {renderFields(EVIDENCE_FIELDS)}
      </section>

      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          className="app-btn-primary rounded-[9px] px-4 py-2 text-xs"
          disabled={disabled || !dirty || hasError}
          aria-busy={save.isPending}
          onClick={() => {
            if (!data) return;
            const body: Record<string, number> = {};
            for (const spec of ALL_FIELDS) body[spec.key] = Number(valueOf(spec.key));
            save.mutate(body);
          }}
        >
          <AsyncLabelSpan busy={save.isPending} idleLabel="Save" busyLabel="Saving…" />
        </button>
        {notice ? (
          <span className={`text-xs ${notice.tone === "ok" ? "text-up" : "text-down"}`}>{notice.text}</span>
        ) : dirty ? (
          <span className="text-xs text-muted">Unsaved changes</span>
        ) : null}
      </div>
    </>
  );
}

function formatFetchedAt(seconds: number): string {
  return new Date(seconds * 1000).toLocaleString("en-IN", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

function WeightsSection() {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: INDEX_SIGNAL_WEIGHTS_QUERY_KEY,
    queryFn: fetchIndexSignalWeights,
    // A refresh runs in the background (SENSEX alone is ~30 BSE calls): poll until it clears.
    refetchInterval: (query) => (query.state.data?.refreshing ? 3_000 : false),
  });
  const refresh = useMutation({
    mutationFn: refreshIndexSignalWeights,
    onSettled: () => void qc.invalidateQueries({ queryKey: INDEX_SIGNAL_WEIGHTS_QUERY_KEY }),
  });
  const refreshing = Boolean(q.data?.refreshing) || refresh.isPending;

  return (
    <section className="app-card space-y-4 p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <h3 className="text-heading font-bold text-foreground">Constituent weights</h3>
          <p className="mt-1 text-xs leading-relaxed text-muted">
            Fetched once per trading day from the exchanges&rsquo; own free-float data: NSE for NIFTY, BSE for
            SENSEX. If that fails the app uses the niftyindices factsheet, then the last good set, then built-in
            seeds. Only the tracked stocks&rsquo; weights relative to each other (&ldquo;share of basket&rdquo;)
            drive the signal.
          </p>
        </div>
        <button
          type="button"
          className="app-btn-outline rounded-[9px] px-4 py-2 text-xs"
          disabled={refreshing}
          aria-busy={refreshing}
          onClick={() => refresh.mutate()}
        >
          <AsyncLabelSpan busy={refreshing} idleLabel="Refresh now" busyLabel="Refreshing…" />
        </button>
      </div>
      {q.error ? (
        <p className="text-xs text-down">{q.error instanceof Error ? q.error.message : "Could not load weights"}</p>
      ) : null}
      {refresh.error ? (
        <p className="text-xs text-down">
          {refresh.error instanceof Error ? refresh.error.message : "Could not start a refresh"}
        </p>
      ) : null}
      <div className="grid gap-5 xl:grid-cols-2">
        {INDICES.map(({ key, name, books }) => (
          <WeightsTable key={key} name={name} books={books} overview={q.data?.indices[key]} />
        ))}
      </div>
    </section>
  );
}

function WeightsTable({
  name,
  books,
  overview,
}: {
  name: string;
  books: string;
  overview: IndexWeightsOverview | undefined;
}) {
  if (!overview) {
    return <p className="text-xs text-muted">Loading {name}…</p>;
  }
  const lastRefresh = overview.last_refresh;
  return (
    <div className="min-w-0 space-y-2">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div className="text-heading font-semibold text-foreground">
          {name} <span className="text-xs font-normal text-muted">· {books}</span>
        </div>
        <div className="text-hint text-muted">
          {WEIGHTS_SOURCE_LABEL[overview.source] ?? overview.source} · as of {overview.as_of}
          {overview.fetched_at ? ` · fetched ${formatFetchedAt(overview.fetched_at)}` : ""}
        </div>
      </div>
      {overview.source === "seed" ? (
        <p className="text-table text-amber-accent">
          Using built-in seed weights: no exchange fetch has succeeded on this instance yet.
        </p>
      ) : null}
      {lastRefresh && !lastRefresh.ok ? (
        <p className="text-table text-down">Last refresh failed: {lastRefresh.errors.join("; ") || "unknown error"}</p>
      ) : null}
      <div className="overflow-x-auto rounded-[10px] border border-border">
        <table className="min-w-full text-left text-table">
          <thead className="app-table-head">
            <tr>
              <th className="px-2.5 py-2 text-right font-semibold whitespace-nowrap">#</th>
              <th className="px-2.5 py-2 font-semibold whitespace-nowrap">Stock</th>
              <th className="px-2.5 py-2 font-semibold whitespace-nowrap">ICICI code</th>
              <th className="px-2.5 py-2 text-right font-semibold whitespace-nowrap">Index weight</th>
              <th className="px-2.5 py-2 text-right font-semibold whitespace-nowrap">Share of basket</th>
            </tr>
          </thead>
          <tbody>
            {overview.tracked.map((row, i) => (
              <tr key={row.symbol} className="app-table-row">
                <td className="px-2.5 py-2 text-right font-mono tabular-nums text-faint">{i + 1}</td>
                <td className="px-2.5 py-2 whitespace-nowrap text-foreground">{row.symbol}</td>
                <td className="px-2.5 py-2 whitespace-nowrap font-mono text-muted">{row.short_name}</td>
                <td className="px-2.5 py-2 text-right font-mono tabular-nums text-foreground">
                  {row.weight.toFixed(2)}%
                </td>
                <td className="px-2.5 py-2 text-right font-mono tabular-nums text-foreground">
                  {row.basket_share != null ? `${row.basket_share.toFixed(1)}%` : "—"}
                </td>
              </tr>
            ))}
            {overview.tracked.length === 0 ? (
              <tr className="app-table-row">
                <td colSpan={5} className="px-2.5 py-3 text-center text-muted">
                  No stocks resolved yet — the scrip master may still be loading.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
      {overview.unresolved.length ? (
        <p className="text-table text-muted">
          Skipped (not in ICICI&rsquo;s scrip master): {overview.unresolved.join(", ")}
        </p>
      ) : null}
    </div>
  );
}

const DAY_OPTIONS = [1, 5, 20, 60];
/** null = each index's breakeven move, the bar the readiness summary uses. */
const MIN_MOVE_OPTIONS: (number | null)[] = [null, 0, 2, 5, 10];
const HORIZONS: { key: string; label: string }[] = [
  { key: "60", label: "+1 min" },
  { key: "300", label: "+5 min" },
  { key: "900", label: "+15 min" },
];
type StateRow = { key: string; label: string; tone: string };
const STATES: StateRow[] = [
  { key: "bullish", label: "Bullish", tone: "text-up" },
  { key: "bearish", label: "Bearish", tone: "text-down" },
  { key: "neutral", label: "Neutral", tone: "text-muted" },
];
const FLIP_STATES: StateRow[] = [
  { key: "bullish", label: "Turned bullish", tone: "text-up" },
  { key: "bearish", label: "Turned bearish", tone: "text-down" },
];
const EXCLUDED_REASONS: { key: keyof ShadowExcluded; label: string }[] = [
  // The log ends before the horizon: the close, or (mid-session) an outcome not due yet.
  { key: "day_end", label: "past the close or not due yet" },
  { key: "no_level", label: "no index level" },
  { key: "gap", label: "gap in the log" },
];

function PillGroup<T extends number | null>({
  label,
  options,
  value,
  onChange,
  format,
}: {
  label: string;
  options: T[];
  value: T;
  onChange: (value: T) => void;
  format: (value: T) => string;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="text-hint text-muted">{label}</span>
      <div role="group" aria-label={label} className="inline-flex gap-1.5">
        {options.map((o) => (
          <button
            key={String(o)}
            type="button"
            aria-pressed={value === o}
            onClick={() => onChange(o)}
            className={`rounded-full border px-3 py-1 text-xs font-medium transition ${
              value === o
                ? "border-accent-strong bg-accent-tint text-accent-strong"
                : "border-border bg-panel text-muted hover:text-foreground"
            }`}
          >
            {format(o)}
          </button>
        ))}
      </div>
    </div>
  );
}

/** The log writes its minute sample on the first publish of each wall-clock minute (backend
 * `shadow_log.record`), so the report is refetched just past each minute rather than on the
 * signal's own publish interval — the publishes in between add nothing to it. */
const MINUTE_SAMPLE_LAG_MS = 5_000;
/** The navbar reads a new state from Redis a moment before the shadow log commits the
 * transition row (`publisher.publish_once` writes the payload first). */
const TRANSITION_SETTLE_MS = 2_000;

function msUntilNextSample(nowMs: number): number {
  const next = (Math.floor((nowMs - MINUTE_SAMPLE_LAG_MS) / 60_000) + 1) * 60_000 + MINUTE_SAMPLE_LAG_MS;
  return next - nowMs;
}

function ShadowEvidenceSection() {
  const qc = useQueryClient();
  const [showDetail, setShowDetail] = useState(false);
  const [days, setDays] = useState(5);
  const [minMove, setMinMove] = useState<number | null>(null);
  const marketStatus = useQuery({
    queryKey: ["settings", "market-status"],
    queryFn: fetchMarketStatus,
    staleTime: 30_000,
    refetchInterval: 30_000,
  });
  const marketOpen = marketStatus.data?.is_open ?? false;
  // No samples are logged while the market is closed, so there is nothing new to fetch.
  const refetchInterval = marketOpen ? () => msUntilNextSample(Date.now()) : false;
  const readiness = useQuery({
    queryKey: INDEX_SIGNAL_READINESS_QUERY_KEY,
    queryFn: fetchIndexSignalReadiness,
    refetchInterval,
  });
  const q = useQuery({
    queryKey: [...INDEX_SIGNAL_SHADOW_REPORT_QUERY_KEY, days, minMove],
    queryFn: () => fetchIndexSignalShadowReport(days, minMove),
    // The full tables are folded away by default: only fetch them once opened.
    enabled: showDetail,
    refetchInterval,
  });

  // A state change writes a transition row the moment it happens: pick it up from the navbar's
  // poll (same query, no extra requests) instead of waiting for the next minute.
  const signals = useIndexQuotes().data?.signals;
  const stateKey = signals ? `${signals.nifty?.state ?? ""}|${signals.sensex?.state ?? ""}` : null;
  const lastStateKey = useRef<string | null>(null);
  useEffect(() => {
    const prev = lastStateKey.current;
    lastStateKey.current = stateKey;
    if (prev == null || stateKey == null || prev === stateKey) return;
    const timer = window.setTimeout(
      () => void qc.invalidateQueries({ queryKey: INDEX_SIGNAL_SHADOW_REPORT_QUERY_KEY }),
      TRANSITION_SETTLE_MS,
    );
    return () => window.clearTimeout(timer);
  }, [stateKey, qc]);

  const requirements = readiness.data?.indices.nifty?.requirements;
  return (
    <section className="app-card space-y-4 p-5">
      <div>
        <h3 className="text-heading font-bold text-foreground">Shadow evidence</h3>
        <p className="mt-1 text-xs leading-relaxed text-muted">
          Is the signal good enough for a scalping bot? Each time it turns bullish or bearish, the index is checked
          5 and 15 minutes later: a call is right when the index went that way by enough to pay for a trade, and it
          only counts for something if it is right more often than simply going along with the market&rsquo;s trend.
          While the market is open this updates every minute, and as soon as the signal changes state.
        </p>
      </div>
      {readiness.error ? (
        <p className="text-xs text-down">
          {readiness.error instanceof Error ? readiness.error.message : "Could not load the verdict"}
        </p>
      ) : null}
      <div className="grid gap-5 xl:grid-cols-2">
        {INDICES.map(({ key, name }) => (
          <ReadinessCard key={key} name={name} readiness={readiness.data?.indices[key]} />
        ))}
      </div>
      {requirements ? (
        <div className="space-y-1 text-hint leading-relaxed text-muted">
          <p className="flex flex-wrap gap-x-4 gap-y-1">
            {(Object.keys(CALL_LABEL) as CallStatus[])
              .filter((s) => s !== "no_calls")
              .map((s) => (
                <span key={s} className="inline-flex items-center gap-1.5">
                  <StatusDot status={s} />
                  {CALL_LABEL[s]}
                </span>
              ))}
          </p>
          <p>
            &ldquo;Ready&rdquo; needs both sides beating the trend within 5 minutes, on at least{" "}
            {requirements.separate_calls} separate calls each, over at least {requirements.sessions} trading days
            with {requirements.up_days} up and {requirements.down_days} down days among them. A separate call is one
            at least 5 minutes after the last one counted. The 15-minute column shows whether the move holds; it
            never decides the verdict. Bots still don&rsquo;t act on the signal until you let them.
          </p>
        </div>
      ) : null}
      <details
        open={showDetail}
        onToggle={(e) => setShowDetail(e.currentTarget.open)}
        className="rounded-[10px] border border-border"
      >
        <summary className="cursor-pointer select-none px-4 py-3 text-xs font-semibold text-foreground">
          Show the full evidence{" "}
          <span className="font-normal text-muted">— every reading, all horizons, CSV download</span>
        </summary>
        <div className="space-y-4 border-t border-border p-4">
          <div>
            <p className="text-xs leading-relaxed text-muted">
              Every minute the signal is logged with the index level, and each reading is compared with where the
              index was 1, 5 and 15 minutes later. A hit is a move of at least the minimum move in the called
              direction; smaller moves are flat and count neither way. &ldquo;Breakeven&rdquo; is the Trading Costs
              round trip as an index move, the same bar the verdict above uses.
            </p>
            <p className="mt-1.5 text-xs leading-relaxed text-muted">
              The range under each hit rate is its 95% range, worked out only from readings a whole horizon apart,
              because back-to-back minutes of one run are nearly the same bet. The edge compares the hit rate with how
              often the index moved that way after any reading in the same period: the signal is only earning its
              keep where it shows green, meaning the whole range sits above that. &ldquo;After a flip&rdquo; scores
              each change of mind once, from the index level at the flip.
            </p>
          </div>
          <div className="flex flex-wrap gap-x-6 gap-y-2">
            <PillGroup label="Period" options={DAY_OPTIONS} value={days} onChange={setDays} format={(d) => `${d}d`} />
            <PillGroup
              label="Minimum move"
              options={MIN_MOVE_OPTIONS}
              value={minMove}
              onChange={setMinMove}
              format={(b) => (b == null ? "Breakeven" : b === 0 ? "Any" : `${b} bps`)}
            />
          </div>
          {q.error ? (
            <p className="text-xs text-down">
              {q.error instanceof Error ? q.error.message : "Could not load the evidence"}
            </p>
          ) : null}
          <div className="grid gap-5 xl:grid-cols-2">
            {INDICES.map(({ key, name }) => (
              <ShadowIndexReport key={key} label={key} name={name} days={days} report={q.data?.indices[key]} />
            ))}
          </div>
        </div>
      </details>
    </section>
  );
}

const HEADLINE: Record<ReadinessStatus, { text: string; tone: string }> = {
  ready: { text: "Yes — it beats the trend", tone: "border-up/40 text-up" },
  too_early: { text: "Not yet — too early to tell", tone: "border-border text-muted" },
  no_edge: { text: "No — no better than the trend", tone: "border-amber-accent/40 text-amber-accent" },
  worse: { text: "No — worse than the trend", tone: "border-down/40 text-down" },
};

const CALL_LABEL: Record<CallStatus, string> = {
  better: "better than the trend",
  worse: "worse than the trend",
  no_edge: "no better than the trend",
  too_early: "too early to tell",
  no_calls: "no calls yet",
};

function StatusDot({ status }: { status: CallStatus }) {
  const fill: Record<CallStatus, string> = {
    better: "bg-up",
    worse: "bg-down",
    no_edge: "bg-faint",
    too_early: "border border-muted",
    no_calls: "border border-muted",
  };
  return <span className={`inline-block size-2 shrink-0 rounded-full ${fill[status]}`} aria-hidden />;
}

function formatNumber(x: number, digits: number): string {
  return x.toLocaleString("en-IN", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

function CallCell({ call, side }: { call: ReadinessCall; side: "bullish" | "bearish" }) {
  const went = side === "bullish" ? "rose" : "fell";
  return (
    <span className="inline-flex items-start gap-2" title={CALL_LABEL[call.status]}>
      <span className="mt-[5px]">
        <StatusDot status={call.status} />
      </span>
      <span className="leading-snug">
        <span className="sr-only">{CALL_LABEL[call.status]}: </span>
        {call.status === "no_calls" ? (
          <span className="text-faint">no calls yet</span>
        ) : (
          <>
            <span className="text-foreground">
              right {call.right} of {call.calls}
            </span>
            {call.trend_share != null ? (
              <span className="block text-hint text-faint">
                market {went} {pct(call.trend_share)} of the time anyway
              </span>
            ) : null}
          </>
        )}
      </span>
    </span>
  );
}

function breakevenLine(r: IndexReadiness): string {
  const be = r.breakeven;
  if (be.points == null || be.bps == null || be.cost_rupees == null) {
    return `Trading costs can't be turned into an index move yet (no lot size or index level), so a move counts from ${formatNumber(r.min_move_bps, 1)} bps.`;
  }
  const priced =
    be.premium != null
      ? `at the last session's ₹${formatNumber(be.premium, 0)} premium`
      : "flat charges only, as there is no option price yet";
  return `A call is right when the index moves at least ${formatNumber(be.points, 1)} points (${formatNumber(be.bps, 1)} bps) its way: enough to cover the ₹${formatNumber(be.cost_rupees, 0)} round trip on one at-the-money lot of ${be.lot_size} (Trading Costs, ${priced}; the bid-ask spread is not included).`;
}

/** Only once every cell has a verdict: what +5 against +15 minutes says about how to trade it. */
function takeaway(r: IndexReadiness): string | null {
  const sides = [r.directions.bullish, r.directions.bearish];
  const decided = (c: ReadinessCall) => c.status !== "too_early" && c.status !== "no_calls";
  if (!sides.every((d) => decided(d.scalp) && decided(d.hold))) return null;
  const quick = sides.every((d) => d.scalp.status === "better");
  const holds = sides.every((d) => d.hold.status === "better");
  if (quick && holds) return "Moves tend to keep going after a flip: winners can be given room to run.";
  if (quick) return "Moves come quickly and fade: take profit within a few minutes.";
  if (holds) return "It calls moves early: they do come, but too slowly for a 5-minute scalp.";
  return null;
}

function stillNeeded(r: IndexReadiness): { share: number; missing: string[] } {
  const req = r.requirements;
  const bull = r.directions.bullish.scalp.separate_calls;
  const bear = r.directions.bearish.scalp.separate_calls;
  const share = Math.min(
    1,
    bull / req.separate_calls,
    bear / req.separate_calls,
    r.sessions / req.sessions,
    r.up_days / req.up_days,
    r.down_days / req.down_days,
  );
  const missing: string[] = [];
  const more = (have: number, need: number, noun: string) => {
    if (have < need) missing.push(`${need - have} more ${noun}${need - have === 1 ? "" : "s"}`);
  };
  more(bull, req.separate_calls, "bullish call");
  more(bear, req.separate_calls, "bearish call");
  more(r.sessions, req.sessions, "trading day");
  more(r.up_days, req.up_days, "up day");
  more(r.down_days, req.down_days, "down day");
  return { share, missing };
}

function ReadinessCard({ name, readiness: r }: { name: string; readiness: IndexReadiness | undefined }) {
  if (!r) {
    return <p className="text-xs text-muted">Loading {name}…</p>;
  }
  const headline = HEADLINE[r.status];
  const { share, missing } = stillNeeded(r);
  const lesson = takeaway(r);
  const flickers = r.flips >= 5 && r.dropped_quickly * 2 >= r.flips;
  return (
    <div className="min-w-0 space-y-3 rounded-[10px] border border-border p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="text-heading font-semibold text-foreground">{name}</div>
        <span className={`rounded-full border px-3 py-1 text-xs font-medium ${headline.tone}`}>
          <span className="font-normal text-muted">Ready to scalp? </span>
          {headline.text}
        </span>
      </div>
      <p className="text-hint leading-relaxed text-muted">{breakevenLine(r)}</p>
      <div className="overflow-x-auto">
        <table className="min-w-full text-left text-table">
          <thead>
            <tr className="text-hint text-muted">
              <th className="py-1.5 pr-3 font-medium" />
              <th className="px-3 py-1.5 font-medium whitespace-nowrap">Within 5 min</th>
              <th className="py-1.5 pl-3 font-medium whitespace-nowrap">Within 15 min</th>
            </tr>
          </thead>
          <tbody>
            {FLIP_STATES.map((s) => {
              const side = s.key as "bullish" | "bearish";
              return (
                <tr key={s.key} className="border-t border-border">
                  <td className={`py-2 pr-3 align-top whitespace-nowrap font-medium ${s.tone}`}>
                    When it {s.label.toLowerCase()}
                  </td>
                  <td className="px-3 py-2 align-top">
                    <CallCell call={r.directions[side].scalp} side={side} />
                  </td>
                  <td className="py-2 pl-3 align-top">
                    <CallCell call={r.directions[side].hold} side={side} />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="space-y-1.5">
        <div className="flex items-center gap-3">
          <span className="shrink-0 text-hint text-muted">Evidence</span>
          <div
            className="h-1.5 flex-1 overflow-hidden rounded-full bg-border"
            role="progressbar"
            aria-label={`${name} evidence collected`}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round(share * 100)}
          >
            <div className="h-full rounded-full bg-accent-strong" style={{ width: `${Math.round(share * 100)}%` }} />
          </div>
        </div>
        <p className="text-hint leading-relaxed text-muted">
          {missing.length ? `Still needed: ${missing.join(", ")}.` : "Enough evidence collected."}
        </p>
      </div>
      {lesson ? <p className="text-table text-foreground">{lesson}</p> : null}
      {r.flips > 0 ? (
        <p className={`text-hint leading-relaxed ${flickers ? "text-amber-accent" : "text-muted"}`}>
          {flickers ? "Changes its mind quickly: " : ""}
          {r.dropped_quickly} of {countLabel(r.flips, "call")} dropped within 5 minutes
          {flickers ? ". A bot acting on every call would pay for each one." : "."}
        </p>
      ) : null}
    </div>
  );
}

function countLabel(n: number, noun: string): string {
  return `${n.toLocaleString("en-IN")} ${noun}${n === 1 ? "" : "s"}`;
}

function ShadowIndexReport({
  label,
  name,
  days,
  report,
}: {
  label: IndexLabel;
  name: string;
  days: number;
  report: ShadowReport | undefined;
}) {
  const download = useMutation({ mutationFn: () => downloadIndexSignalReadings(label, days) });
  return (
    <div className="min-w-0 space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <div className="text-heading font-semibold text-foreground">{name}</div>
          {report ? (
            <div className="text-hint text-muted">
              {countLabel(report.samples, "reading")} · {countLabel(report.transitions, "state change")} ·{" "}
              {countLabel(report.flips, "flip")} · moves from {formatNumber(report.min_move_bps, 1)} bps
            </div>
          ) : null}
        </div>
        <button
          type="button"
          className="app-btn-outline rounded-[9px] px-3 py-1.5 text-xs"
          disabled={download.isPending}
          aria-busy={download.isPending}
          onClick={() => download.mutate()}
          title={`Minute readings for the last ${days}d with the index 1, 5 and 15 minutes later, for Excel`}
        >
          <AsyncLabelSpan busy={download.isPending} idleLabel="Download CSV" busyLabel="Preparing…" />
        </button>
      </div>
      {download.error ? (
        <p className="text-xs text-down">
          {download.error instanceof Error ? download.error.message : "Could not download the readings"}
        </p>
      ) : null}
      {report ? (
        <>
          <EvidenceTable title="Every reading" rows={STATES} cells={report.forward_returns} />
          <EvidenceTable title="After a flip" rows={FLIP_STATES} cells={report.flip_returns} />
          <ShadowFootnotes report={report} />
        </>
      ) : (
        <p className="text-xs text-muted">Loading {name}…</p>
      )}
    </div>
  );
}

function EvidenceTable({
  title,
  rows,
  cells,
}: {
  title: string;
  rows: StateRow[];
  cells: Record<string, Record<string, ShadowBucket>>;
}) {
  return (
    <div className="overflow-x-auto rounded-[10px] border border-border">
      <table className="min-w-full text-left text-table">
        <thead className="app-table-head">
          <tr>
            <th className="px-2.5 py-2 font-semibold whitespace-nowrap">{title}</th>
            {HORIZONS.map((h) => (
              <th key={h.key} className="px-2.5 py-2 text-right font-semibold whitespace-nowrap">
                {h.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((s) => (
            <tr key={s.key} className="app-table-row">
              <td className={`px-2.5 py-2 align-top whitespace-nowrap font-medium ${s.tone}`}>{s.label}</td>
              {HORIZONS.map((h) => (
                <td key={h.key} className="px-2.5 py-2 text-right align-top">
                  <EvidenceCell bucket={cells[h.key]?.[s.key]} directional={s.key !== "neutral"} />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function pct(x: number): string {
  return `${Math.round(x * 100)}%`;
}

function signed(x: number, digits: number): string {
  const rounded = Number(x.toFixed(digits));
  return `${rounded > 0 ? "+" : ""}${(rounded === 0 ? 0 : rounded).toFixed(digits)}`;
}

const VERDICT_TONE: Record<string, string> = { better: "text-up", worse: "text-down", unclear: "text-faint" };
const VERDICT_TITLE: Record<string, string> = {
  better: "The whole 95% range is above how often the index moved this way after any reading",
  worse: "The whole 95% range is below how often the index moved this way after any reading",
  unclear: "The 95% range includes the baseline: no evidence either way yet",
};

function EvidenceCell({ bucket, directional }: { bucket: ShadowBucket | undefined; directional: boolean }) {
  if (!bucket || bucket.n === 0) {
    return <span className="text-faint">—</span>;
  }
  const counts =
    `n ${bucket.n.toLocaleString("en-IN")} · ${bucket.n_independent.toLocaleString("en-IN")} apart` +
    (bucket.flats ? ` · ${bucket.flats.toLocaleString("en-IN")} flat` : "");
  const mean = `${signed(bucket.mean_return_bps, 1)} bps`;
  if (!directional || bucket.hit_rate == null) {
    return (
      <span className="inline-flex flex-col items-end font-mono tabular-nums leading-tight">
        <span className="text-foreground">{directional ? "all flat" : mean}</span>
        <span className="text-hint text-faint">{counts}</span>
      </span>
    );
  }
  const range =
    bucket.hit_rate_low != null && bucket.hit_rate_high != null
      ? `${Math.round(bucket.hit_rate_low * 100)}–${pct(bucket.hit_rate_high)}`
      : null;
  const edge = [
    // Percentage points of hit rate, spelled so it can't be read as index points.
    bucket.edge_hit != null ? `${signed(bucket.edge_hit * 100, 0)}% vs trend` : null,
    bucket.edge_bps != null ? `${signed(bucket.edge_bps, 1)} bps` : null,
  ]
    .filter(Boolean)
    .join(" · ");
  return (
    <span className="inline-flex flex-col items-end font-mono tabular-nums leading-tight">
      <span className="text-foreground">
        {pct(bucket.hit_rate)} hit{range ? <span className="text-hint text-faint"> {range}</span> : null}
      </span>
      {edge ? (
        <span
          className={`text-hint ${VERDICT_TONE[bucket.verdict ?? "unclear"]}`}
          title={VERDICT_TITLE[bucket.verdict ?? "unclear"]}
        >
          edge {edge}
        </span>
      ) : null}
      <span className="text-hint text-faint">{counts}</span>
    </span>
  );
}

function ShadowFootnotes({ report }: { report: ShadowReport }) {
  const baseline = HORIZONS.flatMap((h) => {
    const share = report.baseline[h.key]?.up_share;
    return share == null ? [] : [`${pct(share)} at ${h.label}`];
  });
  const unscored = HORIZONS.flatMap((h) => {
    const excluded = report.excluded[h.key];
    if (!excluded) return [];
    const parts = EXCLUDED_REASONS.filter((r) => excluded[r.key] > 0).map(
      (r) => `${excluded[r.key].toLocaleString("en-IN")} ${r.label}`,
    );
    return parts.length ? [`${h.label}: ${parts.join(", ")}`] : [];
  });
  return (
    <div className="space-y-1 text-hint leading-relaxed text-muted">
      {baseline.length ? (
        <p>Baseline: after any reading, the index rose {baseline.join(" · ")} of the time (flat moves left out).</p>
      ) : null}
      {unscored.length ? <p>No outcome yet: {unscored.join(" · ")}.</p> : null}
    </div>
  );
}
