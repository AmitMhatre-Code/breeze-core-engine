"use client";

import { useMemo, useState, useSyncExternalStore } from "react";
import {
  BarElement,
  CategoryScale,
  Chart as ChartJS,
  Legend,
  LinearScale,
  Tooltip,
  type ChartOptions,
} from "chart.js";
import { Bar } from "react-chartjs-2";
import type { MonthlyPerformanceRow } from "@/lib/performance-data";
import { formatIndianMoneyCompact } from "@/lib/format-money-in";

ChartJS.register(BarElement, CategoryScale, Legend, LinearScale, Tooltip);

function subscribeDarkClass(onChange: () => void): () => void {
  const el = document.documentElement;
  const obs = new MutationObserver(onChange);
  obs.observe(el, { attributes: true, attributeFilter: ["class"] });
  return () => obs.disconnect();
}

function snapshotDarkClass(): boolean {
  return document.documentElement.classList.contains("dark");
}

function serverDarkSnapshot(): boolean {
  return false;
}

function formatLakhTooltip(value: unknown): string {
  const n = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(n)) return "—";
  const inLacs = n / 100_000;
  const formatted =
    inLacs % 1 === 0 ? `${inLacs}L` : `${inLacs.toFixed(2)}L`;
  return `₹${formatted}`;
}

/** Terminal Pro palette (light / dark variants per bar). Chart.js draws to <canvas>, so these
 * must be literal colors — CSS custom properties don't reach canvas rendering. */
function chartPalette(isDark: boolean) {
  return {
    pos: isDark ? "rgba(52, 211, 153, 0.92)" : "rgba(15, 157, 107, 0.88)",
    neg: isDark ? "rgba(248, 113, 113, 0.92)" : "rgba(220, 47, 68, 0.85)",
    brokerage: isDark ? "rgba(251, 191, 36, 0.88)" : "rgba(180, 83, 9, 0.82)",
    taxes: isDark ? "rgba(167, 139, 250, 0.9)" : "rgba(124, 58, 237, 0.82)",
    markings: isDark ? "#8a93a6" : "#5a6473",
    grid: isDark ? "rgba(230,234,242,0.06)" : "rgba(14,21,32,0.06)",
    tickMuted: isDark ? "#5c6577" : "#93a0b0",
    tooltipBg: isDark ? "rgba(22, 27, 36, 0.92)" : "rgba(255, 255, 255, 0.98)",
    tooltipBorder: isDark ? "rgba(35, 42, 54, 0.9)" : "rgba(219, 225, 233, 0.95)",
  };
}

const BAR_RADIUS = 6;
const FONT_STACK =
  '"IBM Plex Sans", ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif';

export function PerformanceMonthlyChart({
  monthly,
}: {
  monthly: MonthlyPerformanceRow[];
}) {
  const [viewMode, setViewMode] = useState<"chart" | "table">("chart");

  const isDark = useSyncExternalStore(
    subscribeDarkClass,
    snapshotDarkClass,
    serverDarkSnapshot,
  );

  const c = useMemo(() => chartPalette(isDark), [isDark]);

  const data = useMemo(() => {
    const labels = monthly.map((m) => m.month);
    const pnl = monthly.map((m) => m.pnl);
    const brokerage = monthly.map((m) => m.brokerage);
    const taxes = monthly.map((m) => m.taxes);
    const bg = pnl.map((v) => (v >= 0 ? c.pos : c.neg));
    return {
      labels,
      datasets: [
        {
          label: "P & L",
          data: pnl,
          backgroundColor: bg,
          stack: "stack0",
          borderRadius: BAR_RADIUS,
          borderSkipped: false,
          maxBarThickness: 22,
        },
        {
          label: "Brokerage",
          data: brokerage,
          backgroundColor: c.brokerage,
          stack: "stack0",
          borderRadius: BAR_RADIUS,
          borderSkipped: false,
          maxBarThickness: 22,
        },
        {
          label: "Taxes",
          data: taxes,
          backgroundColor: c.taxes,
          stack: "stack0",
          borderRadius: BAR_RADIUS,
          borderSkipped: false,
          maxBarThickness: 22,
        },
      ],
    };
  }, [monthly, c]);

  const options: ChartOptions<"bar"> = useMemo(
    () => ({
      indexAxis: "y",
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 520 },
      layout: {
        padding: { top: 4, right: 8, bottom: 8, left: 4 },
      },
      datasets: {
        bar: {
          barPercentage: 0.78,
          categoryPercentage: 0.72,
        },
      },
      plugins: {
        legend: {
          position: "top",
          align: "start",
          labels: {
            color: c.markings,
            font: { family: FONT_STACK, size: 11, weight: 500 },
            padding: 18,
            usePointStyle: true,
            pointStyle: "circle",
            boxWidth: 8,
            boxHeight: 8,
          },
        },
        tooltip: {
          backgroundColor: c.tooltipBg,
          titleColor: c.tickMuted,
          bodyColor: c.markings,
          borderColor: c.tooltipBorder,
          borderWidth: 1,
          padding: 12,
          cornerRadius: 10,
          titleFont: { family: FONT_STACK, size: 10, weight: 600 },
          bodyFont: { family: FONT_STACK, size: 12, weight: 500 },
          displayColors: true,
          boxPadding: 6,
          callbacks: {
            label(ctx) {
              const v = ctx.raw;
              const suffix = formatLakhTooltip(v);
              const label = ctx.dataset.label ?? "";
              return `${label}: ${suffix}`;
            },
          },
        },
      },
      scales: {
        x: {
          stacked: true,
          border: { display: false },
          grid: {
            color: c.grid,
            lineWidth: 1,
            borderDash: [5, 5],
          },
          ticks: {
            color: c.tickMuted,
            font: { family: FONT_STACK, size: 10, weight: 500 },
            padding: 10,
            maxTicksLimit: 8,
            callback(tickValue) {
              const n =
                typeof tickValue === "number"
                  ? tickValue
                  : Number(tickValue);
              if (!Number.isFinite(n)) return "";
              const inLacs = n / 100_000;
              return inLacs % 1 === 0
                ? `${inLacs}L`
                : `${inLacs.toFixed(1)}L`;
            },
          },
        },
        y: {
          stacked: true,
          border: { display: false },
          grid: { display: false },
          ticks: {
            color: c.markings,
            font: { family: FONT_STACK, size: 11, weight: 500 },
            padding: 12,
          },
        },
      },
    }),
    [c],
  );

  if (monthly.length === 0) {
    return (
      <div className="app-card-muted flex min-h-[280px] items-center justify-center p-8 text-sm text-muted">
        No monthly breakdown for this period (no trades in range).
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-end">
        <div className="inline-flex rounded-[9px] border border-border bg-panel2 p-[3px]">
          <button
            type="button"
            className={[
              "rounded-[6px] px-3.5 py-1.5 font-mono text-[11.5px] font-semibold transition",
              viewMode === "chart"
                ? "bg-accent-strong text-accent-ink"
                : "text-muted hover:text-foreground",
            ].join(" ")}
            aria-pressed={viewMode === "chart"}
            onClick={() => setViewMode("chart")}
          >
            Chart
          </button>
          <button
            type="button"
            className={[
              "rounded-[6px] px-3.5 py-1.5 font-mono text-[11.5px] font-semibold transition",
              viewMode === "table"
                ? "bg-accent-strong text-accent-ink"
                : "text-muted hover:text-foreground",
            ].join(" ")}
            aria-pressed={viewMode === "table"}
            onClick={() => setViewMode("table")}
          >
            Table
          </button>
        </div>
      </div>

      {viewMode === "table" ? (
        <div className="overflow-x-auto rounded-[10px] border border-border">
          <table className="min-w-full text-left text-sm">
            <thead className="border-b border-border bg-panel2 text-[10.5px] font-bold uppercase tracking-wide text-faint">
              <tr>
                <th scope="col" className="px-3 py-2">
                  Month
                </th>
                <th scope="col" className="px-3 py-2">
                  P &amp; L
                </th>
                <th scope="col" className="px-3 py-2">
                  Brokerage
                </th>
                <th scope="col" className="px-3 py-2">
                  Taxes
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border-soft">
              {monthly.map((row) => (
                <tr key={row.month}>
                  <td className="px-3 py-1.5 text-foreground">{row.month}</td>
                  <td className="px-3 py-1.5 font-mono tabular-nums text-foreground">
                    {formatIndianMoneyCompact(row.pnl)}
                  </td>
                  <td className="px-3 py-1.5 font-mono tabular-nums text-foreground">
                    {formatIndianMoneyCompact(row.brokerage)}
                  </td>
                  <td className="px-3 py-1.5 font-mono tabular-nums text-foreground">
                    {formatIndianMoneyCompact(row.taxes)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
    <div className="min-h-[min(420px,max(300px,42vh))] w-full min-w-0 rounded-[10px] border border-border bg-panel2 px-3 pb-2 pt-1">
      <Bar key={isDark ? "dark" : "light"} data={data} options={options} />
    </div>
      )}
    </div>
  );
}
