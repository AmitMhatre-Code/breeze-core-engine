"use client";

import { useEffect, useMemo, useRef, useSyncExternalStore } from "react";
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
import type { PeriodChartRow } from "@/lib/performance-data";
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

// Tooltip: each value gets its own natural unit (as-is / K / Lac / Crore).
function formatTooltipMoney(value: unknown): string {
  const n = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(n)) return "—";
  const magnitude = formatIndianMoneyCompact(Math.abs(n), { shortSuffix: true });
  return n < 0 ? `−${magnitude}` : magnitude;
}

// Axis: all ticks share one unit, chosen from the scale's actual rendered
// range — otherwise a small-value month (e.g. a few thousand rupees) renders
// every tick as "₹0.0L" once divided by a fixed 1-Lac unit.
function pickAxisUnit(maxAbs: number): { divisor: number; suffix: string } {
  if (maxAbs >= 1_00_00_000) return { divisor: 1_00_00_000, suffix: "Cr" };
  if (maxAbs >= 1_00_000) return { divisor: 1_00_000, suffix: "L" };
  if (maxAbs >= 1_000) return { divisor: 1_000, suffix: "K" };
  return { divisor: 1, suffix: "" };
}

function formatAxisTick(n: number, unit: { divisor: number; suffix: string }): string {
  if (!Number.isFinite(n)) return "";
  const scaled = Math.abs(n) / unit.divisor;
  const label = unit.suffix
    ? `${scaled % 1 === 0 ? scaled : scaled.toFixed(scaled < 10 ? 2 : 1)}${unit.suffix}`
    : scaled.toLocaleString("en-IN", { maximumFractionDigits: 0 });
  return n < 0 ? `−₹${label}` : `₹${label}`;
}

/** Terminal Pro palette (light / dark variants per bar). Chart.js draws to <canvas>, so these
 * must be literal colors — CSS custom properties don't reach canvas rendering. */
function chartPalette(isDark: boolean) {
  return {
    pos: isDark ? "rgba(52, 211, 153, 0.92)" : "rgba(15, 157, 107, 0.88)",
    neg: isDark ? "rgba(248, 113, 113, 0.92)" : "rgba(220, 47, 68, 0.85)",
    brokerage: isDark ? "rgba(251, 191, 36, 0.88)" : "rgba(180, 83, 9, 0.82)",
    taxes: isDark ? "rgba(129, 140, 248, 0.9)" : "rgba(79, 70, 229, 0.82)",
    markings: isDark ? "#8a93a6" : "#5a6473",
    grid: isDark ? "rgba(230,234,242,0.06)" : "rgba(14,21,32,0.06)",
    tickMuted: isDark ? "#5c6577" : "#93a0b0",
    tooltipBg: isDark ? "rgba(22, 27, 36, 0.92)" : "rgba(255, 255, 255, 0.98)",
    tooltipBorder: isDark ? "rgba(35, 42, 54, 0.9)" : "rgba(219, 225, 233, 0.95)",
  };
}

const BAR_RADIUS = 4;
const FONT_STACK =
  '"IBM Plex Sans", ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif';

export function PerformancePeriodChart({
  rows,
  viewMode,
  periodLabel,
}: {
  rows: PeriodChartRow[];
  viewMode: "chart" | "table";
  /** Column heading for the table view, e.g. "Month" or "Week". */
  periodLabel: string;
}) {
  const isDark = useSyncExternalStore(
    subscribeDarkClass,
    snapshotDarkClass,
    serverDarkSnapshot,
  );

  const chartRef = useRef<ChartJS<"bar"> | null>(null);

  // Chart.js sometimes measures a 0-size canvas on the very first mount inside
  // a flex/grid layout (before the browser's first layout pass settles), then
  // never redraws since later resizes are too small to cross its own redraw
  // threshold — kick a resize once layout has actually settled.
  useEffect(() => {
    const raf = requestAnimationFrame(() => {
      chartRef.current?.resize();
    });
    return () => cancelAnimationFrame(raf);
  }, [viewMode]);

  const c = useMemo(() => chartPalette(isDark), [isDark]);

  const data = useMemo(() => {
    const labels = rows.map((m) => m.label);
    const pnl = rows.map((m) => m.pnl);
    // Brokerage/taxes are costs — anchor them below the zero line regardless
    // of whether the period's net P&L was a gain or a loss (matches the
    // Terminal Pro reference: costs always read as a downward strip at zero).
    // `null` means "no trade data this period" — leave it null so Chart.js
    // skips the bar entirely instead of drawing a zero-height one.
    const brokerage = rows.map((m) => (m.brokerage == null ? null : -Math.abs(m.brokerage)));
    const taxes = rows.map((m) => (m.taxes == null ? null : -Math.abs(m.taxes)));
    const bg = pnl.map((v) => (v == null ? c.pos : v >= 0 ? c.pos : c.neg));
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
          maxBarThickness: 34,
        },
        {
          label: "Brokerage",
          data: brokerage,
          backgroundColor: c.brokerage,
          stack: "stack0",
          borderRadius: BAR_RADIUS,
          borderSkipped: false,
          maxBarThickness: 34,
        },
        {
          label: "Taxes",
          data: taxes,
          backgroundColor: c.taxes,
          stack: "stack0",
          borderRadius: BAR_RADIUS,
          borderSkipped: false,
          maxBarThickness: 34,
        },
      ],
    };
  }, [rows, c]);

  const options: ChartOptions<"bar"> = useMemo(
    () => ({
      indexAxis: "x",
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 520 },
      layout: {
        padding: { top: 4, right: 8, bottom: 4, left: 4 },
      },
      datasets: {
        bar: {
          barPercentage: 0.78,
          categoryPercentage: 0.72,
        },
      },
      plugins: {
        legend: {
          display: false,
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
              const suffix = formatTooltipMoney(ctx.raw);
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
          grid: { display: false },
          ticks: {
            color: c.markings,
            font: { family: FONT_STACK, size: 11, weight: 500 },
            padding: 10,
          },
        },
        y: {
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
            maxTicksLimit: 7,
            callback(tickValue) {
              const n =
                typeof tickValue === "number"
                  ? tickValue
                  : Number(tickValue);
              if (!Number.isFinite(n)) return "";
              const scaleMax = Math.max(Math.abs(this.max ?? 0), Math.abs(this.min ?? 0));
              const unit = pickAxisUnit(scaleMax || Math.abs(n));
              return formatAxisTick(n, unit);
            },
          },
        },
      },
    }),
    [c],
  );

  if (rows.length === 0) {
    return (
      <div className="app-card-muted flex min-h-[280px] items-center justify-center p-8 text-sm text-muted">
        No {periodLabel.toLowerCase()}-by-{periodLabel.toLowerCase()} breakdown
        for this financial year (no trades in range).
      </div>
    );
  }

  const legend = (
    <div className="mb-4 flex flex-wrap items-center gap-x-6 gap-y-2 text-table">
      <span className="inline-flex items-center gap-2">
        <span
          className="inline-block h-2.5 w-2.5 rounded-[2px]"
          style={{ backgroundColor: c.pos }}
        />
        <span style={{ color: c.markings }}>
          P&amp;L <span className="text-muted">(green profit / red loss)</span>
        </span>
      </span>
      <span className="inline-flex items-center gap-2">
        <span
          className="inline-block h-2.5 w-2.5 rounded-[2px]"
          style={{ backgroundColor: c.brokerage }}
        />
        <span style={{ color: c.markings }}>Brokerage</span>
      </span>
      <span className="inline-flex items-center gap-2">
        <span
          className="inline-block h-2.5 w-2.5 rounded-[2px]"
          style={{ backgroundColor: c.taxes }}
        />
        <span style={{ color: c.markings }}>Taxes</span>
      </span>
    </div>
  );

  return viewMode === "table" ? (
    <div>
      {legend}
      <div className="overflow-x-auto">
        <table className="min-w-full text-left text-sm">
          <thead className="border-b border-border-soft text-micro font-bold uppercase tracking-wide text-faint">
            <tr>
              <th scope="col" className="px-3 py-2">
                {periodLabel}
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
            {rows.map((row) => (
              <tr key={row.label}>
                <td className="px-3 py-1.5 text-foreground">{row.label}</td>
                {row.pnl == null ? (
                  <>
                    <td className="px-3 py-1.5 font-mono tabular-nums text-muted">—</td>
                    <td className="px-3 py-1.5 font-mono tabular-nums text-muted">—</td>
                    <td className="px-3 py-1.5 font-mono tabular-nums text-muted">—</td>
                  </>
                ) : (
                  <>
                    <td
                      className={`px-3 py-1.5 font-mono tabular-nums ${row.pnl >= 0 ? "text-up" : "text-down"}`}
                    >
                      {formatIndianMoneyCompact(row.pnl, {
                        shortSuffix: true,
                        skipK: true,
                      })}
                    </td>
                    <td className="px-3 py-1.5 font-mono tabular-nums text-down">
                      −
                      {formatIndianMoneyCompact(Math.abs(row.brokerage ?? 0), {
                        shortSuffix: true,
                        skipK: true,
                      })}
                    </td>
                    <td className="px-3 py-1.5 font-mono tabular-nums text-down">
                      −
                      {formatIndianMoneyCompact(Math.abs(row.taxes ?? 0), {
                        shortSuffix: true,
                        skipK: true,
                      })}
                    </td>
                  </>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  ) : (
    <div>
      {legend}
      <div className="min-h-[min(420px,max(300px,42vh))] w-full min-w-0">
        <Bar
          ref={chartRef}
          key={isDark ? "dark" : "light"}
          data={data}
          options={options}
        />
      </div>
    </div>
  );
}
