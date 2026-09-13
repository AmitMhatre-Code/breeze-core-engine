"use client";

import { useEffect, useMemo, useRef, useSyncExternalStore } from "react";
import {
  CategoryScale,
  Chart as ChartJS,
  Filler,
  LinearScale,
  LineElement,
  PointElement,
  Tooltip,
  type ChartOptions,
} from "chart.js";
import { Line } from "react-chartjs-2";
import { inr, type EquityPoint } from "@/lib/bots-backtest";

ChartJS.register(CategoryScale, Filler, LinearScale, LineElement, PointElement, Tooltip);

function subscribeDarkClass(onChange: () => void): () => void {
  const obs = new MutationObserver(onChange);
  obs.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
  return () => obs.disconnect();
}

const snapshotDark = () => document.documentElement.classList.contains("dark");
const serverSnapshot = () => false;

/** Literal colours: canvas cannot read CSS custom properties (see PerformancePeriodChart). */
function palette(isDark: boolean) {
  return {
    line: isDark ? "rgba(96, 165, 250, 0.95)" : "rgba(37, 99, 235, 0.9)",
    fillUp: isDark ? "rgba(52, 211, 153, 0.12)" : "rgba(15, 157, 107, 0.10)",
    fillDown: isDark ? "rgba(248, 113, 113, 0.12)" : "rgba(220, 47, 68, 0.10)",
    grid: isDark ? "rgba(230,234,242,0.06)" : "rgba(14,21,32,0.06)",
    tick: isDark ? "#5c6577" : "#93a0b0",
    tooltipBg: isDark ? "rgba(22, 27, 36, 0.92)" : "rgba(255, 255, 255, 0.98)",
    tooltipText: isDark ? "#8a93a6" : "#5a6473",
    tooltipBorder: isDark ? "rgba(35, 42, 54, 0.9)" : "rgba(219, 225, 233, 0.95)",
  };
}

const FONT = '"IBM Plex Sans", ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif';

export function BacktestEquityChart({ points }: { points: EquityPoint[] }) {
  const isDark = useSyncExternalStore(subscribeDarkClass, snapshotDark, serverSnapshot);
  const chartRef = useRef<ChartJS<"line"> | null>(null);
  const c = useMemo(() => palette(isDark), [isDark]);

  // Same first-layout resize nudge as PerformancePeriodChart: Chart.js can measure a 0-size
  // canvas inside a grid before layout settles, and then never redraws.
  useEffect(() => {
    const raf = requestAnimationFrame(() => chartRef.current?.resize());
    return () => cancelAnimationFrame(raf);
  }, []);

  const last = points.length ? points[points.length - 1].cumulative : 0;
  // Anchored at zero before the first trade, so the curve reads as P&L from the start of the
  // range and a single-trade run still draws a line.
  const series = useMemo(() => [{ at: "Start", cumulative: 0 }, ...points], [points]);

  const data = useMemo(
    () => ({
      labels: series.map((p) => p.at.slice(0, 16)),
      datasets: [
        {
          label: "Cumulative net P&L",
          data: series.map((p) => p.cumulative),
          borderColor: c.line,
          backgroundColor: last >= 0 ? c.fillUp : c.fillDown,
          fill: "origin" as const,
          borderWidth: 2,
          pointRadius: series.length > 60 ? 0 : 2,
          tension: 0.15,
        },
      ],
    }),
    [series, c, last],
  );

  const options: ChartOptions<"line"> = useMemo(
    () => ({
      responsive: true,
      maintainAspectRatio: false,
      // No animation: the first layout can measure a narrow canvas, and an animated resize then
      // leaves the line drawn in the old strip while the axes span the full width.
      animation: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: c.tooltipBg,
          titleColor: c.tick,
          bodyColor: c.tooltipText,
          borderColor: c.tooltipBorder,
          borderWidth: 1,
          padding: 10,
          titleFont: { family: FONT, size: 10, weight: 600 },
          bodyFont: { family: FONT, size: 12, weight: 500 },
          callbacks: { label: (ctx) => `Net so far: ${inr(Number(ctx.raw))}` },
        },
      },
      scales: {
        x: {
          border: { display: false },
          grid: { display: false },
          ticks: { color: c.tick, font: { family: FONT, size: 10 }, maxTicksLimit: 8, autoSkip: true },
        },
        y: {
          border: { display: false },
          grid: { color: c.grid },
          ticks: {
            color: c.tick,
            font: { family: FONT, size: 10 },
            maxTicksLimit: 6,
            callback: (v) => inr(Number(v)),
          },
        },
      },
    }),
    [c],
  );

  if (points.length === 0) {
    return (
      <div className="app-card-muted flex min-h-[200px] items-center justify-center p-6 text-sm text-muted">
        No trades in this run.
      </div>
    );
  }

  return (
    <div className="h-[min(320px,40vh)] min-h-[220px] w-full min-w-0">
      <Line ref={chartRef} key={isDark ? "dark" : "light"} data={data} options={options} />
    </div>
  );
}
