"use client";

import type { PointerEvent as ReactPointerEvent } from "react";
import { useId, useMemo, useState } from "react";
import { formatIndianMoneyCompact } from "@/lib/format-money-in";

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
  labelledBy?: string;
  describedBy?: string;
  /**
   * Initial fraction of the full [minS, maxS] span shown on the x-axis (centered on spot or midpoint).
   * Zoom out reaches 1 (full range). Default 0.5: when the domain is spot ±40%, the initial view is spot ±20%.
   */
  defaultSpanFraction?: number;
  /** Exact leg strikes to mark as labeled dashed verticals (compact mode only). */
  strikes?: number[];
  /**
   * Space-constrained presentation (Portfolio group payoff): no zoom controls; the expiry
   * P&L line switches from a single green stroke to green-above/red-below zero. Unlike the
   * old minimal preset, compact now renders the same grid/axes/breakevens/T+0 overlay as full
   * mode (via an HTML overlay, since its viewBox is stretched non-uniformly to a fixed height)
   * plus leg-strike markers and a hover crosshair with a rich tooltip.
   */
  compact?: boolean;
};

const X_AXIS_TICKS = 10;

function formatXAxisPrice(v: number): string {
  if (!Number.isFinite(v)) return "—";
  const a = Math.abs(v);
  if (a >= 1000) return v.toFixed(0);
  if (a >= 100) return v.toFixed(1);
  return v.toFixed(2);
}

/** Compact ₹ axis tick, e.g. "+₹4.2k", "−₹1.1L", "₹0" — used by the compact HTML overlay. */
function formatPnlAxisTick(v: number): string {
  if (!Number.isFinite(v)) return "—";
  const sign = v < 0 ? "−" : v > 0 ? "+" : "";
  const a = Math.abs(v);
  const body =
    a >= 1e5
      ? `${(a / 1e5).toFixed(1)}L`
      : a >= 1000
        ? `${(a / 1000).toFixed(a >= 10000 ? 0 : 1)}k`
        : a.toFixed(0);
  return `${sign}₹${body}`;
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

/** Compact mode: the line itself (not just the fill) switches green/red at each zero-crossing. */
function buildTwoTonePayoffLinePaths(
  xs: number[],
  ys: number[],
  xScale: (s: number) => number,
  yScale: (y: number) => number,
): { aboveD: string; belowD: string } {
  if (xs.length < 2 || ys.length !== xs.length) {
    return { aboveD: "", belowD: "" };
  }
  let aboveD = "";
  let belowD = "";
  let sign = ys[0] >= 0 ? 1 : -1;
  let run = `M ${xScale(xs[0])} ${yScale(ys[0])} `;

  const flush = (s: number, d: string) => {
    if (s >= 0) aboveD += d;
    else belowD += d;
  };

  for (let i = 1; i < xs.length; i++) {
    const nextSign = ys[i] >= 0 ? 1 : -1;
    if (nextSign === sign) {
      run += `L ${xScale(xs[i])} ${yScale(ys[i])} `;
      continue;
    }
    const x0 = xs[i - 1];
    const x1 = xs[i];
    const y0 = ys[i - 1];
    const y1 = ys[i];
    const xc = x0 + ((x1 - x0) * -y0) / (y1 - y0);
    const sxc = xScale(xc);
    const syc = yScale(0);
    run += `L ${sxc} ${syc} `;
    flush(sign, run);
    sign = nextSign;
    run = `M ${sxc} ${syc} L ${xScale(xs[i])} ${yScale(ys[i])} `;
  }
  flush(sign, run);

  return { aboveD, belowD };
}

/** Linear-interpolated curve value at an arbitrary x — used to place the compact-mode spot dot. */
function interpolateY(xs: number[], ys: number[], x: number): number | null {
  if (xs.length < 2 || ys.length !== xs.length) return null;
  if (x <= xs[0]) return ys[0];
  if (x >= xs[xs.length - 1]) return ys[xs.length - 1];
  for (let i = 0; i < xs.length - 1; i++) {
    if (x >= xs[i] && x <= xs[i + 1]) {
      const span = xs[i + 1] - xs[i] || 1;
      const t = (x - xs[i]) / span;
      return ys[i] + (ys[i + 1] - ys[i]) * t;
    }
  }
  return null;
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
  strikes,
  labelledBy,
  describedBy,
  compact = false,
}: Props) {
  const clipId = useId().replace(/:/g, "");
  const [spanFrac, setSpanFrac] = useState(() =>
    clamp(defaultSpanFraction, MIN_SPAN_FRAC, 1),
  );
  /** Hovered underlying price from the crosshair (compact mode only); null when the pointer is off-chart. */
  const [hoverPrice, setHoverPrice] = useState<number | null>(null);

  // Full mode reserves room for SVG-text axis labels; compact reserves a slightly
  // narrower gutter for the HTML-overlay axis labels (see the non-uniform-scale note below).
  const PAD_L = compact ? 30 : 40;
  const PAD_R = compact ? 10 : 16;
  const PAD_T = compact ? 11 : 12;
  const PAD_B = compact ? 16 : 22;

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

  const {
    minY,
    maxY,
    pathD,
    pathAboveD,
    pathBelowD,
    pathTodayD,
    spotX,
    spotY,
    breakevenXs,
    xAxisTicks,
    aboveFillD,
    belowFillD,
  } = useMemo(() => {
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
          pathAboveD: "",
          pathBelowD: "",
          pathTodayD: "",
          spotX: spotX0,
          spotY: null as number | null,
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
      const { aboveD: pathAboveD, belowD: pathBelowD } = compact
        ? buildTwoTonePayoffLinePaths(xs, ys, xScale, yScale)
        : { aboveD: "", belowD: "" };

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

      const spotY =
        compact && spot != null && Number.isFinite(spot)
          ? (() => {
              const interp = interpolateY(xs, ys, spot);
              return interp != null ? yScale(interp) : null;
            })()
          : null;

      const breakevenXs = breakevens
        .filter((b) => b >= viewMin && b <= viewMax)
        .map((b) => xScale(b));

      return {
        minY,
        maxY,
        pathD,
        pathAboveD,
        pathBelowD,
        pathTodayD,
        spotX,
        spotY,
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
      compact,
      PAD_L,
      PAD_T,
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
  const yTickEps = Math.max((maxY - minY || 1) * 0.015, 1e-9);

  const yScaleFn = (y: number) =>
    PAD_T + innerH - ((y - minY) / (maxY - minY || 1)) * innerH;
  const xScaleFn = (s: number) =>
    PAD_L + ((s - viewMin) / (viewMax - viewMin || 1)) * innerW;

  const zoomIn = () =>
    setSpanFrac((f) => clamp(f / ZOOM_STEP, MIN_SPAN_FRAC, 1));
  const zoomOut = () =>
    setSpanFrac((f) => clamp(f * ZOOM_STEP, MIN_SPAN_FRAC, 1));

  // Crosshair (compact only): the hit-rect below reports pointer position as a
  // fraction of its own rendered box, which the browser already maps through the
  // SVG's viewBox transform — no manual DOM-to-user-unit scale math needed.
  const handleHoverMove = (e: ReactPointerEvent<SVGRectElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    if (rect.width <= 0) return;
    const frac = clamp((e.clientX - rect.left) / rect.width, 0, 1);
    setHoverPrice(viewMin + frac * (viewMax - viewMin));
  };
  const handleHoverLeave = () => setHoverPrice(null);

  const hoverExpiryY =
    hoverPrice != null ? interpolateY(xs, ys, hoverPrice) : null;
  const hoverTodayY =
    hoverPrice != null && xsToday?.length && ysToday?.length === xsToday.length
      ? interpolateY(xsToday, ysToday, hoverPrice)
      : null;
  const hoverPctFromSpot =
    hoverPrice != null && spot != null && spot > 0
      ? ((hoverPrice - spot) / spot) * 100
      : null;

  return (
    <div className="relative w-full max-w-full">
      {!compact ? (
        <div className="pointer-events-auto absolute right-1 top-1 z-10 flex gap-0.5 rounded-md border border-border bg-panel p-0.5">
          <button
            type="button"
            onClick={zoomIn}
            disabled={!canZoomIn}
            title="Zoom in (narrower price range)"
            aria-label="Zoom in on price axis"
            className="inline-flex size-7 items-center justify-center rounded text-muted transition-colors hover:bg-panel2 disabled:pointer-events-none disabled:text-faint disabled:hover:bg-transparent"
          >
            <ZoomInIcon className="block" />
          </button>
          <button
            type="button"
            onClick={zoomOut}
            disabled={!canZoomOut}
            title="Zoom out (wider price range)"
            aria-label="Zoom out on price axis"
            className="inline-flex size-7 items-center justify-center rounded text-muted transition-colors hover:bg-panel2 disabled:pointer-events-none disabled:text-faint disabled:hover:bg-transparent"
          >
            <ZoomOutIcon className="block" />
          </button>
        </div>
      ) : null}
      <svg
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio={compact ? "none" : undefined}
        className={
          compact
            ? "pointer-events-none block w-full max-w-full text-muted"
            : "pointer-events-none block h-auto w-full max-w-full text-muted"
        }
        style={compact ? { height: H } : undefined}
        role="img"
        aria-labelledby={labelledBy}
        aria-describedby={describedBy}
        aria-label={labelledBy ? undefined : "Payoff diagram"}
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
          {compact ? (
            <>
              <linearGradient
                id={`${clipId}-up-fade`}
                x1="0"
                y1={yScaleFn(0)}
                x2="0"
                y2={PAD_T}
                gradientUnits="userSpaceOnUse"
              >
                <stop offset="0%" style={{ stopColor: "var(--up)", stopOpacity: 0.32 }} />
                <stop offset="100%" style={{ stopColor: "var(--up)", stopOpacity: 0 }} />
              </linearGradient>
              <linearGradient
                id={`${clipId}-down-fade`}
                x1="0"
                y1={yScaleFn(0)}
                x2="0"
                y2={PAD_T + innerH}
                gradientUnits="userSpaceOnUse"
              >
                <stop offset="0%" style={{ stopColor: "var(--down)", stopOpacity: 0.32 }} />
                <stop offset="100%" style={{ stopColor: "var(--down)", stopOpacity: 0 }} />
              </linearGradient>
            </>
          ) : null}
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
                ? "fill-panel2/50"
                : "fill-panel2/80"
            }
          />
          <line
            x1={PAD_L}
            x2={PAD_L + innerW}
            y1={yScaleFn(0)}
            y2={yScaleFn(0)}
            className={
              idle
                ? "stroke-border-soft"
                : "stroke-border"
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
                    ? "stroke-border-soft/60"
                    : "stroke-border-soft"
                }
                strokeWidth={idle ? 0.4 : 0.55}
              />
            );
          })}
          {!idle && belowFillD ? (
            <path
              d={belowFillD}
              className={compact ? undefined : "fill-down/20"}
              fill={compact ? `url(#${clipId}-down-fade)` : undefined}
            />
          ) : null}
          {!idle && aboveFillD ? (
            <path
              d={aboveFillD}
              className={compact ? undefined : "fill-up/20"}
              fill={compact ? `url(#${clipId}-up-fade)` : undefined}
            />
          ) : null}
          {!idle && pathTodayD ? (
            <path
              d={pathTodayD}
              fill="none"
              className="stroke-accent/85"
              strokeWidth={1}
              strokeDasharray="4 3"
            />
          ) : null}
          {!idle && compact ? (
            <>
              {pathBelowD ? (
                <path
                  d={pathBelowD}
                  fill="none"
                  className="stroke-down"
                  strokeWidth={1.15}
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              ) : null}
              {pathAboveD ? (
                <path
                  d={pathAboveD}
                  fill="none"
                  className="stroke-up"
                  strokeWidth={1.15}
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              ) : null}
            </>
          ) : null}
          {!idle && !compact && pathD ? (
            <path
              d={pathD}
              fill="none"
              className="stroke-up"
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
                compact
                  ? "stroke-faint"
                  : idle
                    ? "stroke-faint/40"
                    : "stroke-faint"
              }
              strokeDasharray={compact ? "2 2" : undefined}
              strokeWidth={idle ? 0.75 : 1}
            />
          ) : null}
          {!idle && compact && spotX != null && spotY != null ? (
            <circle
              cx={spotX}
              cy={spotY}
              r={3}
              className="fill-faint"
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
                className="stroke-amber-accent/45"
                strokeWidth={0.65}
                strokeDasharray="4 4"
              />
            ))}
          {!idle && compact && zeroInYRange &&
            breakevenXs.map((bx, i) => (
              <circle
                key={`be-mark-${i}`}
                cx={bx}
                cy={yScaleFn(0)}
                r={3}
                className="fill-amber-accent"
                stroke="var(--panel)"
                strokeWidth={1.1}
              />
            ))}
          {!idle && compact &&
            strikes?.map((k, i) => {
              if (k < viewMin || k > viewMax) return null;
              const x = xScaleFn(k);
              return (
                <line
                  key={`strike-${i}`}
                  x1={x}
                  x2={x}
                  y1={PAD_T}
                  y2={PAD_T + innerH}
                  className="stroke-gtt/55"
                  strokeWidth={0.85}
                  strokeDasharray="3 3"
                />
              );
            })}
          {xAxisTicks.map(({ x }, i) => (
            <line
              key={`x-grid-${i}`}
              x1={x}
              x2={x}
              y1={PAD_T}
              y2={PAD_T + innerH}
              className="stroke-border-soft"
              strokeWidth={0.65}
            />
          ))}
          {!idle && compact && hoverPrice != null ? (
            <>
              <line
                x1={xScaleFn(hoverPrice)}
                x2={xScaleFn(hoverPrice)}
                y1={PAD_T}
                y2={PAD_T + innerH}
                className="stroke-foreground/45"
                strokeWidth={0.75}
                strokeDasharray="2 2"
              />
              {hoverExpiryY != null ? (
                <circle
                  cx={xScaleFn(hoverPrice)}
                  cy={yScaleFn(hoverExpiryY)}
                  r={2.75}
                  className={hoverExpiryY >= 0 ? "fill-up" : "fill-down"}
                  stroke="var(--panel)"
                  strokeWidth={1}
                />
              ) : null}
              {hoverTodayY != null ? (
                <circle
                  cx={xScaleFn(hoverPrice)}
                  cy={yScaleFn(hoverTodayY)}
                  r={2.5}
                  className="fill-accent"
                  stroke="var(--panel)"
                  strokeWidth={1}
                />
              ) : null}
            </>
          ) : null}
          {idle ? (
            <text
              x={PAD_L + innerW / 2}
              y={PAD_T + innerH / 2}
              textAnchor="middle"
              dominantBaseline="middle"
              className="fill-faint text-body"
            >
              Pick a readymade strategy or add legs to see payoff
            </text>
          ) : null}
          {compact && !idle && xs.length ? (
            <rect
              x={PAD_L}
              y={PAD_T}
              width={innerW}
              height={innerH}
              fill="transparent"
              style={{ pointerEvents: "auto", cursor: "crosshair" }}
              onPointerMove={handleHoverMove}
              onPointerDown={handleHoverMove}
              onPointerLeave={handleHoverLeave}
            />
          ) : null}
        </g>
        {!compact && zeroInYRange ? (
          <line
            x1={PAD_L - 6}
            x2={PAD_L}
            y1={yScaleFn(0)}
            y2={yScaleFn(0)}
            className={
              idle
                ? "stroke-muted/70"
                : "stroke-muted"
            }
            strokeWidth={0.9}
          />
        ) : null}
        {!compact &&
          yLabelTicks.map((t, i) => (
            <text
              key={`y-lab-${i}`}
              x={PAD_L - 4}
              y={yScaleFn(t)}
              textAnchor="end"
              dominantBaseline="middle"
              className={
                idle
                  ? "fill-faint font-normal tabular-nums"
                  : Math.abs(t) <= Math.max((maxY - minY || 1) * 0.015, 1e-9)
                    ? "fill-foreground font-semibold tabular-nums"
                    : "fill-muted font-normal tabular-nums"
              }
              fontSize={5.5}
            >
              {t >= 1e5 || t <= -1e5 ? `${(t / 1e5).toFixed(1)}L` : t.toFixed(0)}
            </text>
          ))}
        {!compact &&
          xAxisTicks.map(({ price, x }, i) => (
            <text
              key={`x-lab-${i}`}
              x={x}
              y={H - 5}
              textAnchor="middle"
              className="fill-muted font-normal tabular-nums"
              fontSize={5.5}
            >
              {formatXAxisPrice(price)}
            </text>
          ))}
      </svg>
      {compact ? (
        <div className="pointer-events-none absolute inset-0">
          {yLabelTicks.map((t, i) => (
            <span
              key={`ov-yl-${i}`}
              className={`absolute left-0.5 -translate-y-1/2 whitespace-nowrap font-mono text-micro tabular-nums ${
                Math.abs(t) <= yTickEps ? "font-semibold text-foreground" : "text-faint"
              }`}
              style={{ top: yScaleFn(t) }}
            >
              {formatPnlAxisTick(t)}
            </span>
          ))}
          {xAxisTicks.map(({ price, x }, i) => (
            <span
              key={`ov-xl-${i}`}
              className="absolute -translate-x-1/2 whitespace-nowrap font-mono text-micro tabular-nums text-faint"
              style={{ left: `${(x / W) * 100}%`, top: PAD_T + innerH + 3 }}
            >
              {formatXAxisPrice(price)}
            </span>
          ))}
          {!idle &&
            strikes?.map((k, i) => {
              if (k < viewMin || k > viewMax) return null;
              const x = xScaleFn(k);
              return (
                <span
                  key={`ov-strike-${i}`}
                  className="absolute -translate-x-1/2 whitespace-nowrap rounded-sm bg-panel/90 px-1 font-mono text-micro font-semibold tabular-nums text-gtt ring-1 ring-gtt/30"
                  style={{ left: `${(x / W) * 100}%`, top: PAD_T + 1 }}
                >
                  {formatXAxisPrice(k)}
                </span>
              );
            })}
          {!idle && hoverPrice != null ? (
            <div
              className="absolute z-10 -translate-x-1/2 rounded-md border border-border bg-panel px-2.5 py-2 text-hint shadow-lg"
              style={{
                left: `clamp(4.25rem, ${(xScaleFn(hoverPrice) / W) * 100}%, calc(100% - 4.25rem))`,
                top: PAD_T + 3,
              }}
            >
              <div className="font-mono font-semibold tabular-nums text-foreground">
                {Math.round(hoverPrice).toLocaleString("en-IN")}
              </div>
              {hoverPctFromSpot != null ? (
                <div className="mt-0.5 font-mono tabular-nums text-faint">
                  {hoverPctFromSpot >= 0 ? "+" : ""}
                  {hoverPctFromSpot.toFixed(2)}% vs spot
                </div>
              ) : null}
              {hoverExpiryY != null ? (
                <div className="mt-1.5 flex items-center gap-1.5 whitespace-nowrap">
                  <span className="text-muted">Expiry</span>
                  <span
                    className={`font-semibold tabular-nums ${hoverExpiryY >= 0 ? "text-up" : "text-down"}`}
                  >
                    {formatIndianMoneyCompact(hoverExpiryY)}
                  </span>
                </div>
              ) : null}
              {hoverTodayY != null ? (
                <div className="mt-0.5 flex items-center gap-1.5 whitespace-nowrap">
                  <span className="text-accent">T+0</span>
                  <span
                    className={`font-semibold tabular-nums ${hoverTodayY >= 0 ? "text-up" : "text-down"}`}
                  >
                    {formatIndianMoneyCompact(hoverTodayY)}
                  </span>
                </div>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
