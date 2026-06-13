"use client";

import { useState, type ReactNode } from "react";
import { formatIndianMoneyCompact } from "@/lib/format-money-in";
import { downloadStrategyBuilderAudit } from "@/lib/strategy-builder/api";
import { sb } from "@/lib/strategy-builder/ui";
import type {
  ExecutiveSummary,
  FunnelStage,
  UserExplainabilityReport,
  WhatIfInsight,
  WhyNotStrategy,
  WhyThisStrategy,
} from "@/lib/strategy-builder/types";

const CATEGORY_LABELS: Record<string, string> = {
  income: "Income",
  bullish: "Bullish",
  bearish: "Bearish",
  volatility: "Volatility",
};

function DisclosureSection({
  title,
  level,
  defaultOpen = false,
  children,
}: {
  title: string;
  level: number;
  defaultOpen?: boolean;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="rounded-lg border border-zinc-200/80 bg-zinc-50/60 dark:border-zinc-700/80 dark:bg-zinc-950/40">
      <button
        type="button"
        className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
          Level {level}
        </span>
        <span className="flex-1 text-sm font-medium text-zinc-900 dark:text-zinc-100">
          {title}
        </span>
        <span className="text-zinc-400 dark:text-zinc-500">{open ? "−" : "+"}</span>
      </button>
      {open ? (
        <div className="border-t border-zinc-200/80 px-4 py-3 dark:border-zinc-700/80">
          {children}
        </div>
      ) : null}
    </div>
  );
}

export function FunnelStepper({ funnel }: { funnel: FunnelStage[] }) {
  return (
    <ol className="flex flex-wrap gap-2">
      {funnel.map((stage) => (
        <li
          key={stage.stage}
          className="min-w-[7rem] flex-1 rounded-md border border-zinc-200/90 bg-white px-2.5 py-2 text-center dark:border-zinc-700 dark:bg-zinc-900/80"
        >
          <p className="text-[10px] leading-tight text-zinc-500 dark:text-zinc-400">
            {stage.label}
          </p>
          <p className="mt-0.5 text-sm font-semibold tabular-nums text-zinc-900 dark:text-zinc-100">
            {stage.count === "not_applied" ? "N/A" : stage.count}
          </p>
        </li>
      ))}
    </ol>
  );
}

function WhyThisBlock({ entry }: { entry: WhyThisStrategy }) {
  return (
    <div className="space-y-3 rounded-md border border-emerald-200/60 bg-emerald-50/40 p-3 dark:border-emerald-900/40 dark:bg-emerald-950/20">
      <p className="text-sm font-medium text-zinc-900 dark:text-zinc-100">
        {entry.strategy_name}
      </p>
      {entry.pop_filter_note ? (
        <p className="text-xs text-zinc-500 dark:text-zinc-400">{entry.pop_filter_note}</p>
      ) : null}
      <FunnelStepper funnel={entry.funnel} />
      <div className="space-y-2">
        {entry.returned_trades.map((trade, idx) => (
          <div
            key={`${entry.strategy_id}-${trade.variant_rank ?? idx}`}
            className="rounded-md border border-zinc-200/80 bg-white/80 p-2.5 dark:border-zinc-700 dark:bg-zinc-900/60"
          >
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs font-medium text-zinc-800 dark:text-zinc-200">
                {trade.strategy_name}
                {trade.variant_rank != null && trade.variant_rank > 1
                  ? ` #${trade.variant_rank}`
                  : ""}
              </span>
              {trade.badges?.map((badge) => (
                <span
                  key={badge}
                  className="rounded-full bg-sky-500/10 px-2 py-0.5 text-[10px] font-medium text-sky-700 ring-1 ring-sky-500/20 dark:text-sky-300"
                >
                  {badge}
                </span>
              ))}
            </div>
            {trade.badge_explanations?.length ? (
              <ul className="mt-1.5 space-y-0.5 text-[11px] text-zinc-500 dark:text-zinc-400">
                {trade.badge_explanations.map((b) => (
                  <li key={b.badge}>
                    <span className="font-medium text-zinc-600 dark:text-zinc-300">
                      {b.badge}:
                    </span>{" "}
                    {b.rationale}
                  </li>
                ))}
              </ul>
            ) : null}
            <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-[11px] sm:grid-cols-4">
              <div>
                <dt className="text-zinc-500 dark:text-zinc-400">PoP</dt>
                <dd className="font-medium tabular-nums text-zinc-800 dark:text-zinc-200">
                  {trade.metrics.pop_pct != null ? `${trade.metrics.pop_pct}%` : "—"}
                </dd>
              </div>
              <div>
                <dt className="text-zinc-500 dark:text-zinc-400">Net credit</dt>
                <dd className="font-medium tabular-nums text-zinc-800 dark:text-zinc-200">
                  {trade.metrics.net_credit != null
                    ? formatIndianMoneyCompact(trade.metrics.net_credit)
                    : "—"}
                </dd>
              </div>
              <div>
                <dt className="text-zinc-500 dark:text-zinc-400">Annual ROI</dt>
                <dd className="font-medium tabular-nums text-zinc-800 dark:text-zinc-200">
                  {trade.metrics.annualized_return_pct != null
                    ? `${trade.metrics.annualized_return_pct}%`
                    : "—"}
                </dd>
              </div>
              <div>
                <dt className="text-zinc-500 dark:text-zinc-400">Margin</dt>
                <dd className="font-medium tabular-nums text-zinc-800 dark:text-zinc-200">
                  {trade.metrics.margin != null
                    ? formatIndianMoneyCompact(trade.metrics.margin)
                    : "—"}
                </dd>
              </div>
            </dl>
          </div>
        ))}
      </div>
    </div>
  );
}

function WhyNotBlock({ entry }: { entry: WhyNotStrategy }) {
  return (
    <div className="space-y-2 rounded-md border border-zinc-200/80 bg-white/60 p-3 dark:border-zinc-700 dark:bg-zinc-900/40">
      <p className="text-sm text-zinc-800 dark:text-zinc-200">{entry.explanation}</p>
      <FunnelStepper funnel={entry.funnel} />
    </div>
  );
}

export function ExplainabilityLevel1View({
  executiveSummary,
}: {
  executiveSummary: ExecutiveSummary;
}) {
  const inputs = executiveSummary.user_inputs;

  return (
    <div className="space-y-4">
      <dl className="grid grid-cols-2 gap-3 text-xs sm:grid-cols-4">
        <div>
          <dt className="text-zinc-500 dark:text-zinc-400">Category</dt>
          <dd className="font-medium text-zinc-900 dark:text-zinc-100">
            {CATEGORY_LABELS[inputs.strategy_category] ?? inputs.strategy_category}
          </dd>
        </div>
        <div>
          <dt className="text-zinc-500 dark:text-zinc-400">Capital</dt>
          <dd className="font-medium tabular-nums text-zinc-900 dark:text-zinc-100">
            ₹{inputs.margin_lacs}L
          </dd>
        </div>
        <div>
          <dt className="text-zinc-500 dark:text-zinc-400">Max loss</dt>
          <dd className="font-medium tabular-nums text-zinc-900 dark:text-zinc-100">
            ₹{inputs.max_loss_lacs}L
          </dd>
        </div>
        {inputs.min_pop_pct != null ? (
          <div>
            <dt className="text-zinc-500 dark:text-zinc-400">Min PoP</dt>
            <dd className="font-medium tabular-nums text-zinc-900 dark:text-zinc-100">
              {inputs.min_pop_pct}%
            </dd>
          </div>
        ) : null}
        {inputs.min_ann_return_pct != null ? (
          <div>
            <dt className="text-zinc-500 dark:text-zinc-400">Min annual ROI</dt>
            <dd className="font-medium tabular-nums text-zinc-900 dark:text-zinc-100">
              {inputs.min_ann_return_pct}%
            </dd>
          </div>
        ) : null}
      </dl>

      <div className="flex flex-wrap gap-4 text-xs">
        <p>
          <span className="text-zinc-500 dark:text-zinc-400">Evaluated: </span>
          <span className="font-semibold tabular-nums text-zinc-900 dark:text-zinc-100">
            {executiveSummary.strategies_evaluated}
          </span>
        </p>
        <p>
          <span className="text-zinc-500 dark:text-zinc-400">Recommended: </span>
          <span className="font-semibold text-emerald-700 dark:text-emerald-400">
            {executiveSummary.strategies_recommended.length
              ? executiveSummary.strategies_recommended.map((s) => s.strategy_name).join(", ")
              : "None"}
          </span>
        </p>
        <p>
          <span className="text-zinc-500 dark:text-zinc-400">Skipped: </span>
          <span className="font-semibold tabular-nums text-zinc-900 dark:text-zinc-100">
            {executiveSummary.strategies_skipped.length}
          </span>
        </p>
      </div>

      {executiveSummary.strategies_skipped.length > 0 ? (
        <ul className="space-y-1.5 text-xs text-zinc-600 dark:text-zinc-400">
          {executiveSummary.strategies_skipped.map((s) => (
            <li key={s.strategy_id} className="leading-snug">
              <span className="font-medium text-zinc-800 dark:text-zinc-200">
                {s.strategy_name}:
              </span>{" "}
              {s.summary}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

export function ExplainabilityLevel2View({
  whyThis,
  whyNot,
}: {
  whyThis: WhyThisStrategy[];
  whyNot: WhyNotStrategy[];
}) {
  if (whyThis.length === 0 && whyNot.length === 0) {
    return (
      <p className="text-sm text-zinc-500 dark:text-zinc-400">
        No strategy decision details are available for this build.
      </p>
    );
  }

  return (
    <div className="space-y-4">
      {whyThis.length > 0 ? (
        <div className="space-y-3">
          <p className="text-xs font-semibold uppercase tracking-wide text-emerald-700 dark:text-emerald-400">
            Recommended
          </p>
          {whyThis.map((entry) => (
            <WhyThisBlock key={entry.strategy_id} entry={entry} />
          ))}
        </div>
      ) : null}
      {whyNot.length > 0 ? (
        <div className="space-y-3">
          <p className="text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
            Skipped
          </p>
          {whyNot.map((entry) => (
            <div key={entry.strategy_id}>
              <p className="mb-1 text-xs font-medium text-zinc-700 dark:text-zinc-300">
                {entry.strategy_name}
              </p>
              <WhyNotBlock entry={entry} />
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

export function ExplainabilityLevel3View({ insights }: { insights: WhatIfInsight[] }) {
  if (insights.length === 0) {
    return (
      <p className="text-sm text-zinc-500 dark:text-zinc-400">
        No constraint sensitivity insights are available for this build.
      </p>
    );
  }

  return (
    <ul className="space-y-2">
      {insights.map((insight, idx) => (
        <li
          key={`${insight.constraint}-${idx}`}
          className="rounded-md border border-amber-200/70 bg-amber-50/50 px-3 py-2 text-xs leading-snug text-zinc-700 dark:border-amber-900/40 dark:bg-amber-950/20 dark:text-zinc-300"
        >
          {insight.message}
        </li>
      ))}
    </ul>
  );
}

export function StrategyExplainabilityPanel({
  report,
  auditSessionId,
}: {
  report: UserExplainabilityReport;
  auditSessionId?: string | null;
}) {
  const [auditDownloading, setAuditDownloading] = useState(false);
  const [auditError, setAuditError] = useState<string | null>(null);

  return (
    <div className={`${sb.section} space-y-3`} id="strategy-builder-explainability">
      <h3 className="text-sm font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
        Decision transparency
      </h3>

      <DisclosureSection title="Executive summary" level={1} defaultOpen>
        <ExplainabilityLevel1View executiveSummary={report.executive_summary} />
      </DisclosureSection>

      {(report.why_this.length > 0 || report.why_not.length > 0) && (
        <DisclosureSection title="Why this strategy? / Why not?" level={2}>
          <ExplainabilityLevel2View
            whyThis={report.why_this}
            whyNot={report.why_not}
          />
        </DisclosureSection>
      )}

      {report.what_if_insights.length > 0 ? (
        <DisclosureSection title="What if?" level={3}>
          <ExplainabilityLevel3View insights={report.what_if_insights} />
        </DisclosureSection>
      ) : null}

      {auditSessionId ? (
        <DisclosureSection title="Technical audit" level={4}>
          <p className="text-xs text-zinc-600 dark:text-zinc-400">
            Download the full technical audit JSON for this strategy-builder session.
          </p>
          <button
            type="button"
            className="mt-2 font-normal text-sky-600 underline underline-offset-2 hover:text-sky-500 disabled:cursor-wait disabled:opacity-60 dark:text-sky-400 dark:hover:text-sky-300"
            title="Download full technical audit JSON"
            disabled={auditDownloading}
            onClick={() => {
              void (async () => {
                setAuditError(null);
                setAuditDownloading(true);
                try {
                  await downloadStrategyBuilderAudit(auditSessionId);
                } catch (e) {
                  const msg =
                    e instanceof Error ? e.message : "Failed to download audit log";
                  setAuditError(msg);
                } finally {
                  setAuditDownloading(false);
                }
              })();
            }}
          >
            {auditDownloading ? "downloading…" : "download audit"}
          </button>
          {auditError ? (
            <p className="mt-2 text-sm text-red-600 dark:text-red-400">{auditError}</p>
          ) : null}
        </DisclosureSection>
      ) : null}
    </div>
  );
}
