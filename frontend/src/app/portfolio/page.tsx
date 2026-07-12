// Client component so auth cookies are included with browser fetch.
"use client";

import { useLayoutEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { AppShell } from "@/components/layout/AppShell";
import { apiClient } from "@/lib/api-client";
import {
  OpenPositionsTable,
  type PortfolioPositionsViewMode,
} from "@/components/portfolio/OpenPositionsTable";
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
import { usePnlRecomputeRefetchMs } from "@/lib/portfolio/usePnlRecomputeRefetchMs";

type IciciApiResponse = {
  Status: number;
  Error?: string;
  Success?: {
    positions?: PortfolioPositionRecord[];
  };
};

const POSITIONS_VIEW_MODE_STORAGE_KEY = "breeze-core-engine-portfolio-view";

export default function PortfolioPage() {
  // Collapsed groups have no live chain subscription, so this poll is their only
  // refresh path — follows the same Settings > Advanced P&L recompute interval
  // the live overlay uses, so every figure on the page shares one cadence.
  const portfolioPollMs = usePnlRecomputeRefetchMs();
  const q = useQuery({
    queryKey: ["portfolio", "positions"],
    queryFn: async () => apiClient.get<IciciApiResponse>("/portfolio/data"),
    refetchInterval: portfolioPollMs,
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
  const [positionsViewMode, setPositionsViewMode] =
    useState<PortfolioPositionsViewMode>("grouped");

  useLayoutEffect(() => {
    const stored = localStorage.getItem(POSITIONS_VIEW_MODE_STORAGE_KEY);
    if (stored === "grouped" || stored === "individual") {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- sync after layout boot script, matches ThemeProvider
      setPositionsViewMode(stored);
    }
  }, []);

  const setViewModeAndPersist = (mode: PortfolioPositionsViewMode) => {
    setPositionsViewMode(mode);
    localStorage.setItem(POSITIONS_VIEW_MODE_STORAGE_KEY, mode);
  };

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
            <header className="flex items-center justify-between border-b border-border-soft px-4 py-2.5">
              <h2 className="text-heading font-semibold uppercase tracking-wide text-faint">
                Open positions
              </h2>
              <PositionsViewModeToggle
                value={positionsViewMode}
                onChange={setViewModeAndPersist}
              />
            </header>
            <div className="p-0">
              <OpenPositionsTable
                positions={positions}
                defaultExpandedGroupKey={topGroupKey}
                onLiveGroupCountChange={setLiveGroupCount}
                viewMode={positionsViewMode}
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

function PositionsViewModeToggle({
  value,
  onChange,
}: {
  value: PortfolioPositionsViewMode;
  onChange: (mode: PortfolioPositionsViewMode) => void;
}) {
  const grouped = value === "grouped";
  return (
    <label className="flex shrink-0 items-center gap-2">
      <span className="text-micro font-semibold uppercase tracking-wide text-faint">
        Group legs
      </span>
      <button
        type="button"
        role="switch"
        aria-checked={grouped}
        aria-label="Group legs"
        onClick={() => onChange(grouped ? "individual" : "grouped")}
        className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition ${
          grouped ? "bg-accent-strong" : "bg-border"
        }`}
      >
        <span
          className={`inline-block size-5 transform rounded-full bg-white shadow transition ${
            grouped ? "translate-x-[22px]" : "translate-x-0.5"
          }`}
        />
      </button>
    </label>
  );
}
