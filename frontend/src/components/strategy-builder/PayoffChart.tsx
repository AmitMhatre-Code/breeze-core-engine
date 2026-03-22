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
  strikes: number[];
  minS: number;
  maxS: number;
  height?: number;
};

/** Left padding: room for y-axis tick labels + vertical axis title. */
const PAD_L = 46;
const PAD_R = 16;
const PAD_T = 12;
const PAD_B = 28;

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
  strikes,
  minS,
  maxS,
  height = 220,
}: Props) {
  const W = 640;
  const H = height;
  const innerW = W - PAD_L - PAD_R;
  const innerH = H - PAD_T - PAD_B;

  const { minY, maxY, pathD, pathTodayD, spotX, breakevenXs, strikeXs } =
    useMemo(() => {
      const xScale = (s: number) =>
        PAD_L + ((s - minS) / (maxS - minS || 1)) * innerW;

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
          strikeXs: [] as { strike: number; x: number }[],
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

      const strikeXs = [...new Set(strikes)].map((k) => ({
        strike: k,
        x: xScale(clamp(k, minS, maxS)),
      }));

      return { minY, maxY, pathD, pathTodayD, spotX, breakevenXs, strikeXs };
    }, [
      idle,
      xs,
      ys,
      xsToday,
      ysToday,
      spot,
      breakevens,
      strikes,
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
      {strikeXs.map(({ strike, x }) => (
        <g key={strike}>
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
            {strike}
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
      <text
        x={PAD_L + innerW / 2}
        y={PAD_T + innerH + 3}
        textAnchor="middle"
        dominantBaseline="hanging"
        className={
          idle
            ? "fill-zinc-500/70 font-normal dark:fill-zinc-500/60"
            : "fill-zinc-500 font-normal dark:fill-zinc-500"
        }
        fontSize={6.5}
      >
        Underlying at expiry
      </text>
      <text
        x={PAD_L - 2}
        y={PAD_T + innerH / 2}
        transform={`rotate(-90 ${PAD_L - 2} ${PAD_T + innerH / 2})`}
        textAnchor="middle"
        className="fill-zinc-500 font-normal dark:fill-zinc-500"
        fontSize={6.5}
      >
        P&amp;L
      </text>
    </svg>
  );
}
