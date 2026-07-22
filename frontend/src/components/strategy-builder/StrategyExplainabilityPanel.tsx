"use client";

import { useState, type ReactNode } from "react";
import { PopLabel } from "@/components/strategy-builder/PopLabel";
import { formatIndianMoneyCompact } from "@/lib/format-money-in";
import { downloadStrategyBuilderAudit } from "@/lib/strategy-builder/api";
import { displayPopLabel } from "@/lib/strategy-builder/pop-help";
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
    <div className="rounded-lg border border-border-soft bg-panel2">
      <button
        type="button"
        className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="text-xs font-semibold uppercase tracking-wide text-faint">
          Level {level}
        </span>
        <span className="flex-1 text-sm font-medium text-foreground">
          {title}
        </span>
        <span className="text-faint">{open ? "−" : "+"}</span>
      </button>
      {open ? (
        <div className="border-t border-border-soft px-4 py-3">{children}</div>
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
          className="min-w-[7rem] flex-1 rounded-md border border-border bg-panel px-2.5 py-2 text-center"
        >
          <p className="text-body leading-tight text-faint">
            {displayPopLabel(stage.label)}
          </p>
          <p className="mt-0.5 font-mono text-sm font-semibold tabular-nums text-foreground">
            {stage.count === "not_applied" ? "N/A" : stage.count}
          </p>
        </li>
      ))}
    </ol>
  );
}

function WhyThisTradeMetrics({
  trade,
  showPopInfo,
}: {
  trade: WhyThisStrategy["returned_trades"][number];
  showPopInfo: boolean;
}) {
  return (
    <>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs font-medium text-foreground">
          {trade.strategy_name}
          {trade.variant_rank != null && trade.variant_rank > 1
            ? ` #${trade.variant_rank}`
            : ""}
        </span>
        {trade.badges?.map((badge) => (
          <span
            key={badge}
            className="rounded-full bg-accent-tint px-2 py-0.5 text-body font-medium text-accent-strong"
          >
            {badge}
          </span>
        ))}
      </div>
      {trade.badge_explanations?.length ? (
        <ul className="mt-1.5 space-y-0.5 text-heading text-faint">
          {trade.badge_explanations.map((b) => (
            <li key={b.badge}>
              <span className="font-medium text-muted">{b.badge}:</span>{" "}
              {b.rationale}
            </li>
          ))}
        </ul>
      ) : null}
      <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-heading sm:grid-cols-4">
        <div>
          <dt className="text-faint">
            <PopLabel variant="inline" showInfo={showPopInfo} />
          </dt>
          <dd className="font-mono font-medium tabular-nums text-foreground">
            {trade.metrics.pop_pct != null ? `${trade.metrics.pop_pct}%` : "—"}
          </dd>
        </div>
        <div>
          <dt className="text-faint">Net credit</dt>
          <dd className="font-mono font-medium tabular-nums text-foreground">
            {trade.metrics.net_credit != null
              ? formatIndianMoneyCompact(trade.metrics.net_credit)
              : "—"}
          </dd>
        </div>
        <div>
          <dt className="text-faint">Annual ROI</dt>
          <dd className="font-mono font-medium tabular-nums text-foreground">
            {trade.metrics.annualized_return_pct != null
              ? `${trade.metrics.annualized_return_pct}%`
              : "—"}
          </dd>
        </div>
        <div>
          <dt className="text-faint">Margin</dt>
          <dd className="font-mono font-medium tabular-nums text-foreground">
            {trade.metrics.margin != null
              ? formatIndianMoneyCompact(trade.metrics.margin)
              : "—"}
          </dd>
        </div>
      </dl>
    </>
  );
}

function WhyThisBlock({ entry }: { entry: WhyThisStrategy }) {
  return (
    <div className="space-y-3 rounded-md border border-up/30 bg-up-tint p-3">
      <p className="text-sm font-medium text-foreground">
        {entry.strategy_name}
      </p>
      {entry.pop_filter_note ? (
        <p className="text-xs text-faint">{entry.pop_filter_note}</p>
      ) : null}
      <FunnelStepper funnel={entry.funnel} />
      <div className="space-y-2">
        {entry.returned_trades.map((trade, idx) => (
          <div
            key={`${entry.strategy_id}-${trade.variant_rank ?? idx}`}
            className="rounded-md border border-border-soft bg-panel p-2.5"
          >
            <WhyThisTradeMetrics trade={trade} showPopInfo={idx === 0} />
          </div>
        ))}
      </div>
    </div>
  );
}

function WhyNotBlock({ entry }: { entry: WhyNotStrategy }) {
  return (
    <div className="space-y-2 rounded-md border border-border-soft bg-panel2 p-3">
      <p className="text-sm text-muted">{entry.explanation}</p>
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
          <dt className="text-faint">Category</dt>
          <dd className="font-medium text-foreground">
            {CATEGORY_LABELS[inputs.strategy_category] ?? inputs.strategy_category}
          </dd>
        </div>
        <div>
          <dt className="text-faint">Capital</dt>
          <dd className="font-mono font-medium tabular-nums text-foreground">
            ₹{inputs.margin_lacs}L
          </dd>
        </div>
        <div>
          <dt className="text-faint">Max loss</dt>
          <dd className="font-mono font-medium tabular-nums text-foreground">
            {inputs.allow_infinite_loss
              ? "Infinite loss"
              : inputs.max_loss_lacs != null
                ? `₹${inputs.max_loss_lacs}L`
                : "—"}
          </dd>
        </div>
        {inputs.min_pop_pct != null ? (
          <div>
            <dt className="text-faint">
              <PopLabel variant="field" showInfo />
            </dt>
            <dd className="font-mono font-medium tabular-nums text-foreground">
              {inputs.min_pop_pct}%
            </dd>
          </div>
        ) : null}
        {inputs.min_ann_return_pct != null ? (
          <div>
            <dt className="text-faint">Min annual ROI</dt>
            <dd className="font-mono font-medium tabular-nums text-foreground">
              {inputs.min_ann_return_pct}%
            </dd>
          </div>
        ) : null}
      </dl>

      <div className="flex flex-wrap gap-4 text-xs">
        <p>
          <span className="text-faint">Evaluated: </span>
          <span className="font-mono font-semibold tabular-nums text-foreground">
            {executiveSummary.strategies_evaluated}
          </span>
        </p>
        <p>
          <span className="text-faint">Recommended: </span>
          <span className="font-semibold text-up">
            {executiveSummary.strategies_recommended.length
              ? executiveSummary.strategies_recommended.map((s) => s.strategy_name).join(", ")
              : "None"}
          </span>
        </p>
        <p>
          <span className="text-faint">Skipped: </span>
          <span className="font-mono font-semibold tabular-nums text-foreground">
            {executiveSummary.strategies_skipped.length}
          </span>
        </p>
        {executiveSummary.generation_duration_seconds != null ? (
          <p>
            <span className="text-faint">Generated in: </span>
            <span className="font-mono font-semibold tabular-nums text-foreground">
              {executiveSummary.generation_duration_seconds.toFixed(1)} s
            </span>
          </p>
        ) : null}
      </div>

      {executiveSummary.strategies_skipped.length > 0 ? (
        <ul className="space-y-1.5 text-xs text-muted">
          {executiveSummary.strategies_skipped.map((s) => (
            <li key={s.strategy_id} className="leading-snug">
              <span className="font-medium text-foreground">
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
      <p className="text-sm text-muted">
        No strategy decision details are available for this build.
      </p>
    );
  }

  return (
    <div className="space-y-4">
      {whyThis.length > 0 ? (
        <div className="space-y-3">
          <p className="text-xs font-semibold uppercase tracking-wide text-up">
            Recommended
          </p>
          {whyThis.map((entry) => (
            <WhyThisBlock key={entry.strategy_id} entry={entry} />
          ))}
        </div>
      ) : null}
      {whyNot.length > 0 ? (
        <div className="space-y-3">
          <p className="text-xs font-semibold uppercase tracking-wide text-faint">
            Skipped
          </p>
          {whyNot.map((entry) => (
            <div key={entry.strategy_id}>
              <p className="mb-1 text-xs font-medium text-muted">
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
      <p className="text-sm text-muted">
        No constraint sensitivity insights are available for this build.
      </p>
    );
  }

  return (
    <ul className="space-y-2">
      {insights.map((insight, idx) => (
        <li
          key={`${insight.constraint}-${idx}`}
          className="rounded-md border border-amber-accent/40 bg-amber-tint px-3 py-2 text-xs leading-snug text-amber-on-tint"
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
    <div className="space-y-3" id="strategy-builder-explainability">
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
          <p className="text-xs text-muted">
            Download the full technical audit JSON for this strategy-builder session.
          </p>
          <button
            type="button"
            className="mt-2 font-normal text-accent-strong underline underline-offset-2 transition hover:brightness-110 disabled:cursor-wait disabled:opacity-60"
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
            <p className="mt-2 text-sm text-down">{auditError}</p>
          ) : null}
        </DisclosureSection>
      ) : null}
    </div>
  );
}
