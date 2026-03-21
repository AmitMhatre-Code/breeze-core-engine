"use client";

import { useMemo, useSyncExternalStore } from "react";
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

/** Softer, app-aligned palette (light / dark variants per bar) */
function chartPalette(isDark: boolean) {
  return {
    pos: isDark ? "rgba(52, 211, 153, 0.92)" : "rgba(16, 185, 129, 0.88)",
    neg: isDark ? "rgba(251, 113, 133, 0.92)" : "rgba(244, 63, 94, 0.85)",
    brokerage: isDark ? "rgba(251, 191, 36, 0.88)" : "rgba(245, 158, 11, 0.82)",
    taxes: isDark ? "rgba(129, 140, 248, 0.9)" : "rgba(99, 102, 241, 0.82)",
    markings: isDark ? "#a1a1aa" : "#64748b",
    grid: isDark ? "rgba(255,255,255,0.06)" : "rgba(15,23,42,0.06)",
    tickMuted: isDark ? "#71717a" : "#94a3b8",
    tooltipBg: isDark ? "rgba(24, 24, 27, 0.92)" : "rgba(255, 255, 255, 0.98)",
    tooltipBorder: isDark ? "rgba(63, 63, 70, 0.9)" : "rgba(228, 228, 231, 0.95)",
  };
}

const BAR_RADIUS = 6;
const FONT_STACK =
  'ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif';

export function PerformanceMonthlyChart({
  monthly,
}: {
  monthly: MonthlyPerformanceRow[];
}) {
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
      <div className="app-card-muted flex min-h-[280px] items-center justify-center p-8 text-sm text-zinc-500 dark:text-zinc-400">
        No monthly breakdown for this period (no trades in range).
      </div>
    );
  }

  return (
    <div className="min-h-[min(420px,max(300px,42vh))] w-full min-w-0 rounded-2xl border border-zinc-200/90 bg-gradient-to-b from-zinc-50/90 to-white/40 px-3 pb-2 pt-1 dark:border-zinc-800/90 dark:from-zinc-950/50 dark:to-zinc-900/20">
      <Bar key={isDark ? "dark" : "light"} data={data} options={options} />
    </div>
  );
}
