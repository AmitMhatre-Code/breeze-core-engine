// Client component so auth cookies are included with browser fetch.
"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { AppShell } from "@/components/layout/AppShell";
import { apiClient } from "@/lib/api-client";
import { OpenPositionsTable } from "@/components/portfolio/OpenPositionsTable";
import type { PortfolioPositionRecord } from "@/lib/portfolio";
import {
  buildPortfolioPositionGroups,
  pickTopGroupKey,
} from "@/lib/portfolio/groupPositions";
import {
  computePortfolioTotals,
  formatSignedRupees,
} from "@/lib/portfolio/totals";
import { formatIndianMoneyCompact } from "@/lib/format-money-in";

type IciciApiResponse = {
  Status: number;
  Error?: string;
  Success?: {
    positions?: PortfolioPositionRecord[];
  };
};

/** Base positions poll — collapsed groups have no live chain subscription, so this is their only refresh path. */
const PORTFOLIO_POLL_MS = 30_000;

export default function PortfolioPage() {
  const q = useQuery({
    queryKey: ["portfolio", "positions"],
    queryFn: async () => apiClient.get<IciciApiResponse>("/portfolio/data"),
    refetchInterval: PORTFOLIO_POLL_MS,
  });

  const data = q.data;
  const positions = useMemo(
    () => data?.Success?.positions ?? [],
    [data?.Success?.positions],
  );
  const groups = useMemo(
    () => buildPortfolioPositionGroups(positions),
    [positions],
  );
  const totals = useMemo(() => computePortfolioTotals(groups), [groups]);
  const topGroupKey = useMemo(() => pickTopGroupKey(groups), [groups]);
  const [liveGroupCount, setLiveGroupCount] = useState(0);

  return (
    <AppShell>
      {q.isLoading ? (
        <div className="app-card space-y-3 p-4">
          <div className="h-5 w-32 app-skeleton rounded-sm border-0" />
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="h-9 w-full app-skeleton rounded-sm border-0" />
          ))}
        </div>
      ) : q.error ? (
        <div className="app-alert-error">
          Unable to load portfolio:{" "}
          {q.error instanceof Error ? q.error.message : "Unknown error"}
        </div>
      ) : (
        <div className="space-y-4">
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div>
              <h1 className="app-text-title">Portfolio</h1>
              <p className="mt-0.5 text-sm app-text-muted">
                Open positions in NFO/BFO options only
              </p>
            </div>
            <div className="flex items-center gap-3">
              {data && data.Status !== 200 ? (
                <span className="text-xs text-down">
                  {data.Error || "Unable to load portfolio"}
                </span>
              ) : null}
              {liveGroupCount > 0 ? (
                <span className="flex items-center gap-1.5 text-xs text-muted">
                  <span className="relative inline-flex size-2 shrink-0">
                    <span className="absolute inline-flex size-full animate-ping rounded-full bg-up opacity-75" />
                    <span className="relative inline-flex size-2 rounded-full bg-up" />
                  </span>
                  Live quotes · WebSocket
                </span>
              ) : null}
            </div>
          </div>

          <PortfolioSummaryPanel totals={totals} />

          <section className="app-card min-w-0 overflow-hidden">
            <header className="border-b border-border-soft px-4 py-2.5">
              <h2 className="text-heading font-semibold uppercase tracking-wide text-faint">
                Open positions
              </h2>
            </header>
            <div className="p-0">
              <OpenPositionsTable
                positions={positions}
                defaultExpandedGroupKey={topGroupKey}
                onLiveGroupCountChange={setLiveGroupCount}
              />
            </div>
          </section>
        </div>
      )}
    </AppShell>
  );
}

function PortfolioSummaryPanel({
  totals,
}: {
  totals: ReturnType<typeof computePortfolioTotals>;
}) {
  const mtm = formatSignedRupees(totals.totalMtm);
  const carry = formatSignedRupees(totals.totalCarry);
  const legLabel = `${totals.legCount} leg${totals.legCount === 1 ? "" : "s"}`;

  return (
    <div className="app-card grid min-w-0 grid-cols-2 divide-x divide-y divide-border-soft sm:grid-cols-4 sm:divide-y-0">
      <SummaryTile
        label="Total MTM"
        value={mtm.text}
        valueClassName={mtm.className}
        caption={`${totals.groupCount} group${totals.groupCount === 1 ? "" : "s"} · ${legLabel}`}
      />
      <SummaryTile
        label="Total carry"
        value={carry.text}
        valueClassName={carry.className}
        caption="If held to expiry"
      />
      <SummaryTile
        label="Span + ELM margin"
        value={
          totals.totalMargin != null
            ? formatIndianMoneyCompact(totals.totalMargin, { shortSuffix: true })
            : "—"
        }
        valueClassName="text-foreground"
        caption="Blocked across positions"
      />
      <SummaryTile
        label="Carry return"
        value={
          totals.carryReturnPct != null
            ? `${totals.carryReturnPct.toFixed(1)}%`
            : "—"
        }
        valueClassName="text-accent-strong"
        caption="Annualised on margin"
      />
    </div>
  );
}

function SummaryTile({
  label,
  value,
  valueClassName,
  caption,
}: {
  label: string;
  value: string;
  valueClassName: string;
  caption: string;
}) {
  return (
    <div className="p-4">
      <div className="text-heading uppercase tracking-wide text-faint">
        {label}
      </div>
      <div
        className={`mt-1.5 font-mono text-title font-semibold tabular-nums ${valueClassName}`}
      >
        {value}
      </div>
      <div className="mt-1.5 text-heading app-text-muted">{caption}</div>
    </div>
  );
}
