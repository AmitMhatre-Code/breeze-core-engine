"use client";

import { useMemo } from "react";

type Props = {
  /** No legs yet — muted chrome only, no payoff curves or breakevens. */
  idle?: boolean;
  xs: number[];
  ys: number[];
  xsToday?: number[];
  ysToday?: number[];
  spot: number | null;
  breakevens: number[];
  minS: number;
  maxS: number;
  height?: number;
};

/** Left padding: room for y-axis tick labels. */
const PAD_L = 40;
const PAD_R = 16;
const PAD_T = 12;
const PAD_B = 22;

const X_AXIS_TICKS = 10;

function formatXAxisPrice(v: number): string {
  if (!Number.isFinite(v)) return "—";
  const a = Math.abs(v);
  if (a >= 1000) return v.toFixed(0);
  if (a >= 100) return v.toFixed(1);
  return v.toFixed(2);
}

/** Fills between expiry P&amp;L polyline and y=0: green above zero, red below. */
function buildPayoffFillPaths(
  xs: number[],
  ys: number[],
  xScale: (s: number) => number,
  yScale: (y: number) => number,
): { aboveD: string; belowD: string } {
  if (xs.length < 2 || ys.length !== xs.length) {
    return { aboveD: "", belowD: "" };
  }
  const yZ = yScale(0);
  let aboveD = "";
  let belowD = "";

  for (let i = 0; i < xs.length - 1; i++) {
    const x0 = xs[i];
    const x1 = xs[i + 1];
    const d0 = ys[i];
    const d1 = ys[i + 1];
    if (d0 === 0 && d1 === 0) continue;

    const sx0 = xScale(x0);
    const sx1 = xScale(x1);
    const sy0 = yScale(d0);
    const sy1 = yScale(d1);

    if (d0 * d1 < 0) {
      const xc = x0 + ((x1 - x0) * -d0) / (d1 - d0);
      const sxc = xScale(xc);
      if (d0 > 0) {
        aboveD += `M ${sx0} ${sy0} L ${sxc} ${yZ} L ${sx0} ${yZ} Z `;
        belowD += `M ${sxc} ${yZ} L ${sx1} ${sy1} L ${sx1} ${yZ} Z `;
      } else {
        belowD += `M ${sx0} ${sy0} L ${sxc} ${yZ} L ${sx0} ${yZ} Z `;
        aboveD += `M ${sxc} ${yZ} L ${sx1} ${sy1} L ${sx1} ${yZ} Z `;
      }
      continue;
    }

    const quad = `M ${sx0} ${sy0} L ${sx1} ${sy1} L ${sx1} ${yZ} L ${sx0} ${yZ} Z `;
    if (d0 >= 0 && d1 >= 0) {
      aboveD += quad;
    } else if (d0 <= 0 && d1 <= 0) {
      belowD += quad;
    }
  }

  return { aboveD, belowD };
}

function clamp(v: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, v));
}

export function PayoffChart({
  idle = false,
  xs,
  ys,
  xsToday,
  ysToday,
  spot,
  breakevens,
  minS,
  maxS,
  height = 220,
}: Props) {
  const W = 640;
  const H = height;
  const innerW = W - PAD_L - PAD_R;
  const innerH = H - PAD_T - PAD_B;

  const { minY, maxY, pathD, pathTodayD, spotX, breakevenXs, xAxisTicks, aboveFillD, belowFillD } =
    useMemo(() => {
      const span = maxS - minS || 1;
      const xScale = (s: number) => PAD_L + ((s - minS) / span) * innerW;

      const xAxisTicksInner = Array.from({ length: X_AXIS_TICKS }, (_, i) => {
        const price = minS + (span * i) / (X_AXIS_TICKS - 1);
        return { price, x: xScale(price) };
      });

      if (idle) {
        const minY = -1;
        const maxY = 1;
        const spotX0 =
          spot != null && Number.isFinite(spot)
            ? xScale(clamp(spot, minS, maxS))
            : null;
        return {
          minY,
          maxY,
          pathD: "",
          pathTodayD: "",
          spotX: spotX0,
          breakevenXs: [] as number[],
          xAxisTicks: xAxisTicksInner,
          aboveFillD: "",
          belowFillD: "",
        };
      }

      const allY = [...ys, ...(ysToday ?? [])];
      let lo = Math.min(...allY, 0);
      let hi = Math.max(...allY, 0);
      if (lo === hi) {
        lo -= 1;
        hi += 1;
      }
      const pad = (hi - lo) * 0.08;
      const minY = lo - pad;
      const maxY = hi + pad;

      const yScale = (y: number) =>
        PAD_T + innerH - ((y - minY) / (maxY - minY || 1)) * innerH;

      const { aboveD, belowD } = buildPayoffFillPaths(xs, ys, xScale, yScale);

      const pts = xs.map((s, i) => `${xScale(s).toFixed(1)},${yScale(ys[i]).toFixed(1)}`);
      const pathD = pts.length ? `M ${pts.join(" L ")}` : "";

      let pathTodayD = "";
      if (xsToday?.length && ysToday?.length === xsToday.length) {
        const pts2 = xsToday.map((s, i) =>
          `${xScale(s).toFixed(1)},${yScale(ysToday[i]).toFixed(1)}`,
        );
        pathTodayD = pts2.length ? `M ${pts2.join(" L ")}` : "";
      }

      const spotX =
        spot != null && Number.isFinite(spot)
          ? xScale(clamp(spot, minS, maxS))
          : null;

      const breakevenXs = breakevens
        .filter((b) => b >= minS && b <= maxS)
        .map((b) => xScale(b));

      return {
        minY,
        maxY,
        pathD,
        pathTodayD,
        spotX,
        breakevenXs,
        xAxisTicks: xAxisTicksInner,
        aboveFillD: aboveD,
        belowFillD: belowD,
      };
    }, [
      idle,
      xs,
      ys,
      xsToday,
      ysToday,
      spot,
      breakevens,
      minS,
      maxS,
      innerW,
      innerH,
    ]);

  const yTicks = useMemo(() => {
    const n = 4;
    const ticks: number[] = [];
    for (let i = 0; i <= n; i++) {
      ticks.push(minY + ((maxY - minY) * i) / n);
    }
    return ticks;
  }, [minY, maxY]);

  const yScaleFn = (y: number) =>
    PAD_T + innerH - ((y - minY) / (maxY - minY || 1)) * innerH;

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      className="pointer-events-none h-auto w-full max-w-full text-zinc-500 dark:text-zinc-400"
      role="img"
      aria-label="Payoff diagram"
    >
      <rect
        x={PAD_L}
        y={PAD_T}
        width={innerW}
        height={innerH}
        rx={6}
        className={
          idle
            ? "fill-zinc-100/50 dark:fill-zinc-950/40"
            : "fill-zinc-100/80 dark:fill-zinc-950/50"
        }
      />
      {/* zero line */}
      <line
        x1={PAD_L}
        x2={PAD_L + innerW}
        y1={yScaleFn(0)}
        y2={yScaleFn(0)}
        className={
          idle
            ? "stroke-zinc-200/70 dark:stroke-zinc-700/50"
            : "stroke-zinc-300 dark:stroke-zinc-600"
        }
        strokeDasharray="4 4"
        strokeWidth={idle ? 0.6 : 0.75}
      />
      {yTicks.map((t, i) => (
        <g key={i}>
          <line
            x1={PAD_L}
            x2={PAD_L + innerW}
            y1={yScaleFn(t)}
            y2={yScaleFn(t)}
            className={
              idle
                ? "stroke-zinc-200/45 dark:stroke-zinc-800/40"
                : "stroke-zinc-200/80 dark:stroke-zinc-800/80"
            }
            strokeWidth={idle ? 0.4 : 0.55}
          />
          <text
            x={PAD_L - 4}
            y={yScaleFn(t)}
            textAnchor="end"
            dominantBaseline="middle"
            className={
              idle
                ? "fill-zinc-400 font-normal tabular-nums dark:fill-zinc-600"
                : "fill-zinc-500 font-normal tabular-nums dark:fill-zinc-500"
            }
            fontSize={5.5}
          >
            {t >= 1e5 || t <= -1e5 ? `${(t / 1e5).toFixed(1)}L` : t.toFixed(0)}
          </text>
        </g>
      ))}
      {!idle && belowFillD ? (
        <path
          d={belowFillD}
          className="fill-rose-500/22 dark:fill-rose-400/18"
        />
      ) : null}
      {!idle && aboveFillD ? (
        <path
          d={aboveFillD}
          className="fill-emerald-500/24 dark:fill-emerald-400/18"
        />
      ) : null}
      {!idle && pathTodayD ? (
        <path
          d={pathTodayD}
          fill="none"
          className="stroke-violet-500/85 dark:stroke-violet-400/80"
          strokeWidth={1}
          strokeDasharray="4 3"
        />
      ) : null}
      {!idle && pathD ? (
        <path
          d={pathD}
          fill="none"
          className="stroke-emerald-600/92 dark:stroke-emerald-400/88"
          strokeWidth={1.15}
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      ) : null}
      {spotX != null ? (
        <line
          x1={spotX}
          x2={spotX}
          y1={PAD_T}
          y2={PAD_T + innerH}
          className={
            idle
              ? "stroke-sky-500/25 dark:stroke-sky-400/20"
              : "stroke-sky-500/75 dark:stroke-sky-400/70"
          }
          strokeWidth={idle ? 0.75 : 1}
        />
      ) : null}
      {!idle &&
        breakevenXs.map((bx, i) => (
          <line
            key={`be-${i}`}
            x1={bx}
            x2={bx}
            y1={PAD_T}
            y2={PAD_T + innerH}
            className="stroke-amber-500/38 dark:stroke-amber-400/32"
            strokeWidth={0.65}
            strokeDasharray="4 4"
          />
        ))}
      {xAxisTicks.map(({ price, x }, i) => (
        <g key={`x-tick-${i}`}>
          <line
            x1={x}
            x2={x}
            y1={PAD_T}
            y2={PAD_T + innerH}
            className="stroke-zinc-400/50 dark:stroke-zinc-500/40"
            strokeWidth={0.65}
          />
          <text
            x={x}
            y={H - 5}
            textAnchor="middle"
            className="fill-zinc-600 font-normal tabular-nums dark:fill-zinc-400"
            fontSize={5.5}
          >
            {formatXAxisPrice(price)}
          </text>
        </g>
      ))}
      {idle ? (
        <text
          x={PAD_L + innerW / 2}
          y={PAD_T + innerH / 2}
          textAnchor="middle"
          dominantBaseline="middle"
          className="fill-zinc-400 text-[10px] dark:fill-zinc-500"
        >
          Pick a readymade strategy or add legs to see payoff
        </text>
      ) : null}
    </svg>
  );
}
