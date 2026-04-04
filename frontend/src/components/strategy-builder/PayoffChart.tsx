"use client";

import { useId, useMemo, useState } from "react";

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
  /**
   * Initial fraction of the full [minS, maxS] span shown on the x-axis (centered on spot or midpoint).
   * Zoom out reaches 1 (full range). Default 0.5 tightens a wide domain until the user zooms out.
   */
  defaultSpanFraction?: number;
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

const MIN_SPAN_FRAC = 0.12;
const ZOOM_STEP = 1.18;

function computeViewRange(
  minS: number,
  maxS: number,
  center: number,
  spanFrac: number,
): { viewMin: number; viewMax: number } {
  const full = maxS - minS || 1;
  const span = full * spanFrac;
  let lo = center - span / 2;
  let hi = center + span / 2;
  if (lo < minS) {
    lo = minS;
    hi = minS + span;
    if (hi > maxS) {
      hi = maxS;
      lo = maxS - span;
    }
  } else if (hi > maxS) {
    hi = maxS;
    lo = maxS - span;
    if (lo < minS) lo = minS;
  }
  return { viewMin: lo, viewMax: hi };
}

function ZoomInIcon({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <circle cx="11" cy="11" r="7" />
      <path d="M11 8v6M8 11h6" />
      <path d="m21 21-3.5-3.5" />
    </svg>
  );
}

function ZoomOutIcon({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <circle cx="11" cy="11" r="7" />
      <path d="M8 11h6" />
      <path d="m21 21-3.5-3.5" />
    </svg>
  );
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
  defaultSpanFraction = 0.5,
}: Props) {
  const clipId = useId().replace(/:/g, "");
  const [spanFrac, setSpanFrac] = useState(() =>
    clamp(defaultSpanFraction, MIN_SPAN_FRAC, 1),
  );

  const W = 640;
  const H = height;
  const innerW = W - PAD_L - PAD_R;
  const innerH = H - PAD_T - PAD_B;

  const centerS =
    spot != null && Number.isFinite(spot) ? spot : (minS + maxS) / 2;
  const { viewMin, viewMax } = useMemo(
    () => computeViewRange(minS, maxS, centerS, spanFrac),
    [minS, maxS, centerS, spanFrac],
  );

  const canZoomIn = spanFrac > MIN_SPAN_FRAC * 1.001;
  const canZoomOut = spanFrac < 1 - 1e-9;

  const { minY, maxY, pathD, pathTodayD, spotX, breakevenXs, xAxisTicks, aboveFillD, belowFillD } =
    useMemo(() => {
      const span = viewMax - viewMin || 1;
      const xScale = (s: number) => PAD_L + ((s - viewMin) / span) * innerW;

      const xAxisTicksInner = Array.from({ length: X_AXIS_TICKS }, (_, i) => {
        const price = viewMin + (span * i) / (X_AXIS_TICKS - 1);
        return { price, x: xScale(price) };
      });

      if (idle) {
        const minY = -1;
        const maxY = 1;
        const spotX0 =
          spot != null &&
          Number.isFinite(spot) &&
          spot >= viewMin &&
          spot <= viewMax
            ? xScale(spot)
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

      const visY: number[] = [0];
      for (let i = 0; i < xs.length; i++) {
        const x = xs[i];
        if (x >= viewMin && x <= viewMax) {
          visY.push(ys[i]);
          if (ysToday?.length === xs.length) visY.push(ysToday[i]);
        }
      }
      if (visY.length <= 1 && ys.length) {
        visY.push(...ys, ...(ysToday ?? []));
      }
      let lo = Math.min(...visY);
      let hi = Math.max(...visY);
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
        spot != null &&
        Number.isFinite(spot) &&
        spot >= viewMin &&
        spot <= viewMax
          ? xScale(spot)
          : null;

      const breakevenXs = breakevens
        .filter((b) => b >= viewMin && b <= viewMax)
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
      viewMin,
      viewMax,
      innerW,
      innerH,
    ]);

  const yGridTicks = useMemo(() => {
    const n = 4;
    const ticks: number[] = [];
    for (let i = 0; i <= n; i++) {
      ticks.push(minY + ((maxY - minY) * i) / n);
    }
    return ticks;
  }, [minY, maxY]);

  const yLabelTicks = useMemo(() => {
    const span = maxY - minY || 1;
    const eps = Math.max(span * 0.015, 1e-9);
    const ticks = [...yGridTicks];
    if (minY <= 0 && maxY >= 0 && !ticks.some((t) => Math.abs(t) <= eps)) {
      ticks.push(0);
    }
    ticks.sort((a, b) => a - b);
    return ticks;
  }, [yGridTicks, minY, maxY]);

  const zeroInYRange = minY <= 0 && maxY >= 0;

  const yScaleFn = (y: number) =>
    PAD_T + innerH - ((y - minY) / (maxY - minY || 1)) * innerH;

  const zoomIn = () =>
    setSpanFrac((f) => clamp(f / ZOOM_STEP, MIN_SPAN_FRAC, 1));
  const zoomOut = () =>
    setSpanFrac((f) => clamp(f * ZOOM_STEP, MIN_SPAN_FRAC, 1));

  return (
    <div className="relative w-full max-w-full">
      <div className="pointer-events-auto absolute right-1 top-1 z-10 flex gap-0.5 rounded-md border border-zinc-200/90 bg-white/95 p-0.5 shadow-sm ring-1 ring-zinc-950/5 dark:border-zinc-600 dark:bg-zinc-900/95 dark:ring-white/10">
        <button
          type="button"
          onClick={zoomIn}
          disabled={!canZoomIn}
          title="Zoom in (narrower price range)"
          aria-label="Zoom in on price axis"
          className="inline-flex size-7 items-center justify-center rounded text-zinc-600 transition-colors hover:bg-zinc-100 disabled:pointer-events-none disabled:text-zinc-300 disabled:hover:bg-transparent dark:text-zinc-300 dark:hover:bg-zinc-800 dark:disabled:text-zinc-600 dark:disabled:hover:bg-transparent"
        >
          <ZoomInIcon className="block" />
        </button>
        <button
          type="button"
          onClick={zoomOut}
          disabled={!canZoomOut}
          title="Zoom out (wider price range)"
          aria-label="Zoom out on price axis"
          className="inline-flex size-7 items-center justify-center rounded text-zinc-600 transition-colors hover:bg-zinc-100 disabled:pointer-events-none disabled:text-zinc-300 disabled:hover:bg-transparent dark:text-zinc-300 dark:hover:bg-zinc-800 dark:disabled:text-zinc-600 dark:disabled:hover:bg-transparent"
        >
          <ZoomOutIcon className="block" />
        </button>
      </div>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="pointer-events-none h-auto w-full max-w-full text-zinc-500 dark:text-zinc-400"
        role="img"
        aria-label="Payoff diagram"
      >
        <defs>
          <clipPath id={clipId}>
            <rect
              x={PAD_L}
              y={PAD_T}
              width={innerW}
              height={innerH}
              rx={6}
            />
          </clipPath>
        </defs>
        <g clipPath={`url(#${clipId})`}>
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
          {yGridTicks.map((t, i) => {
            const span = maxY - minY || 1;
            const onZero =
              Math.abs(t) <= Math.max(span * 0.015, 1e-9) && zeroInYRange;
            if (onZero) return null;
            return (
              <line
                key={`y-grid-${i}`}
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
            );
          })}
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
          {xAxisTicks.map(({ x }, i) => (
            <line
              key={`x-grid-${i}`}
              x1={x}
              x2={x}
              y1={PAD_T}
              y2={PAD_T + innerH}
              className="stroke-zinc-400/50 dark:stroke-zinc-500/40"
              strokeWidth={0.65}
            />
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
        </g>
        {zeroInYRange ? (
          <line
            x1={PAD_L - 6}
            x2={PAD_L}
            y1={yScaleFn(0)}
            y2={yScaleFn(0)}
            className={
              idle
                ? "stroke-zinc-500/80 dark:stroke-zinc-500/60"
                : "stroke-zinc-600 dark:stroke-zinc-400"
            }
            strokeWidth={0.9}
          />
        ) : null}
        {yLabelTicks.map((t, i) => (
          <text
            key={`y-lab-${i}`}
            x={PAD_L - 4}
            y={yScaleFn(t)}
            textAnchor="end"
            dominantBaseline="middle"
            className={
              idle
                ? "fill-zinc-400 font-normal tabular-nums dark:fill-zinc-600"
                : Math.abs(t) <= Math.max((maxY - minY || 1) * 0.015, 1e-9)
                  ? "fill-zinc-700 font-semibold tabular-nums dark:fill-zinc-200"
                  : "fill-zinc-500 font-normal tabular-nums dark:fill-zinc-500"
            }
            fontSize={5.5}
          >
            {t >= 1e5 || t <= -1e5 ? `${(t / 1e5).toFixed(1)}L` : t.toFixed(0)}
          </text>
        ))}
        {xAxisTicks.map(({ price, x }, i) => (
          <text
            key={`x-lab-${i}`}
            x={x}
            y={H - 5}
            textAnchor="middle"
            className="fill-zinc-600 font-normal tabular-nums dark:fill-zinc-400"
            fontSize={5.5}
          >
            {formatXAxisPrice(price)}
          </text>
        ))}
      </svg>
    </div>
  );
}
