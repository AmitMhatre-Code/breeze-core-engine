"use client";

import type { KeyboardEvent, PointerEvent } from "react";
import {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { DashboardChartSkeleton } from "@/components/dashboard/DashboardLoading";

type Point = { date: string; value: number };

const W = 560;
const H = 148;
const PAD_L = 40;
const PAD_R = 0;
const PAD_T = 8;
const PAD_B = 28;

const LINE_BLUE = "#3b82f6";
const LINE_WIDTH = 1.25;
/** Target on-screen px for axis text (matches ~`text-[10px]` / small widget copy; SVG viewBox scaling otherwise blows it up). */
const AXIS_LABEL_SCREEN_PX = 10;
const HOVER_LABEL_SCREEN_PX = 10;
const AREA_TOP_OPACITY = 0.38;
const AREA_BOTTOM_OPACITY = 0.03;

/** Catmull–Rom → cubic Béziers (open curve). */
function smoothPathThrough(
  pts: ReadonlyArray<readonly [number, number]>,
): string {
  if (pts.length === 0) return "";
  if (pts.length === 1) return `M ${pts[0][0]} ${pts[0][1]}`;
  let d = `M ${pts[0][0]} ${pts[0][1]}`;
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[i === 0 ? 0 : i - 1];
    const p1 = pts[i];
    const p2 = pts[i + 1];
    const p3 = pts[Math.min(i + 2, pts.length - 1)];
    const cp1x = p1[0] + (p2[0] - p0[0]) / 6;
    const cp1y = p1[1] + (p2[1] - p0[1]) / 6;
    const cp2x = p2[0] - (p3[0] - p1[0]) / 6;
    const cp2y = p2[1] - (p3[1] - p1[1]) / 6;
    d += ` C ${cp1x} ${cp1y}, ${cp2x} ${cp2y}, ${p2[0]} ${p2[1]}`;
  }
  return d;
}

function buildAreaPath(
  smoothTop: string,
  firstX: number,
  firstY: number,
  lastX: number,
  lastY: number,
  yBase: number,
  cornerR: number,
): string {
  const r = Math.max(0, cornerR);
  const span = lastX - firstX;
  const rr = span >= 2 * r ? r : Math.max(0, span / 2 - 0.5);
  if (rr <= 0.5) {
    return `${smoothTop} L ${lastX} ${yBase} L ${firstX} ${yBase} Z`;
  }
  return [
    smoothTop,
    `L ${lastX} ${yBase - rr}`,
    `Q ${lastX} ${yBase} ${lastX - rr} ${yBase}`,
    `L ${firstX + rr} ${yBase}`,
    `Q ${firstX} ${yBase} ${firstX} ${yBase - rr}`,
    `L ${firstX} ${firstY}`,
    "Z",
  ].join(" ");
}

const MONTHS = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
] as const;

/**
 * WebKit/Safari often reports `getBoundingClientRect().width === 0` on `<svg>` on the
 * first layout pass even when the chart paints. Prefer the block wrapper’s
 * `clientWidth` / `offsetWidth`, then SVG rect, then parent.
 */
function readChartLayoutWidthPx(
  container: HTMLElement | null,
  svg: SVGSVGElement | null,
): number {
  let w = 0;
  if (container) {
    w = Math.max(
      w,
      container.clientWidth,
      container.offsetWidth,
      container.getBoundingClientRect().width,
    );
  }
  if (svg) {
    w = Math.max(w, svg.getBoundingClientRect().width);
    const sw = svg.width?.baseVal?.value;
    if (typeof sw === "number" && sw > 0) w = Math.max(w, sw);
  }
  const parent = container?.parentElement;
  if (parent) {
    w = Math.max(
      w,
      parent.clientWidth,
      (parent as HTMLElement).offsetWidth,
      parent.getBoundingClientRect().width,
    );
  }
  return w;
}

/** When layout width is still 0 (Safari first paint), cap by viewport so label font is never oversized. */
function viewportWidthFallbackPx(): number {
  if (typeof window === "undefined") return W;
  return Math.min(window.innerWidth, 2000);
}

function monthStartTickIndices(series: Point[]): { i: number; label: string }[] {
  const out: { i: number; label: string }[] = [];
  let prevKey = "";
  for (let i = 0; i < series.length; i++) {
    const d = series[i].date.slice(0, 10);
    const [y, m, day] = d.split("-");
    if (!y || !m || !day) continue;
    const key = `${y}-${m}`;
    if (key === prevKey) continue;
    prevKey = key;
    const mi = parseInt(m, 10) - 1;
    const mon = MONTHS[mi] ?? m;
    out.push({ i, label: `${day}-${mon}` });
  }
  return out;
}

export function Vix30dChart({
  series,
  loading = false,
}: {
  series: Point[];
  loading?: boolean;
}) {
  const [hoverI, setHoverI] = useState<number | null>(null);
  const [viewMode, setViewMode] = useState<"chart" | "table">("chart");
  const [keyboardI, setKeyboardI] = useState<number | null>(null);
  const gradId = useId().replace(/:/g, "");
  const containerRef = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  /**
   * CSS width for scaling SVG label font. Starts at `W` for stable SSR/hydration;
   * refined from layout, with `innerWidth` fallback when Safari reports 0 until
   * a later pass (avoids invisible labels and avoids oversized text).
   */
  const [svgClientW, setSvgClientW] = useState(W);

  useLayoutEffect(() => {
    const applyWidth = () => {
      const m = readChartLayoutWidthPx(containerRef.current, svgRef.current);
      const cap = viewportWidthFallbackPx();
      const next = m > 0 ? m : cap;
      setSvgClientW((prev) =>
        Math.abs(prev - next) < 0.25 ? prev : next,
      );
    };

    applyWidth();
    const t0 = window.setTimeout(applyWidth, 0);
    const t1 = window.setTimeout(applyWidth, 50);
    const t2 = window.setTimeout(applyWidth, 150);

    let raf = 0;
    let n = 0;
    const retry = () => {
      applyWidth();
      if (readChartLayoutWidthPx(containerRef.current, svgRef.current) > 0)
        return;
      if (n++ < 48) raf = requestAnimationFrame(retry);
    };
    if (readChartLayoutWidthPx(containerRef.current, svgRef.current) <= 0) {
      raf = requestAnimationFrame(retry);
    }

    const onWinResize = () => applyWidth();
    window.addEventListener("resize", onWinResize);

    let io: IntersectionObserver | undefined;
    const wrap = containerRef.current;
    if (wrap && typeof IntersectionObserver !== "undefined") {
      io = new IntersectionObserver(
        (entries) => {
          if (entries.some((e) => e.isIntersecting)) applyWidth();
        },
        { rootMargin: "80px", threshold: 0 },
      );
      io.observe(wrap);
    }

    const targets: Element[] = [];
    if (wrap) targets.push(wrap);
    const svg = svgRef.current;
    if (svg) targets.push(svg);

    const ros: ResizeObserver[] = [];
    if (typeof ResizeObserver !== "undefined" && targets.length) {
      const ro = new ResizeObserver(applyWidth);
      for (const t of targets) ro.observe(t);
      ros.push(ro);
    }

    return () => {
      window.removeEventListener("resize", onWinResize);
      window.clearTimeout(t0);
      window.clearTimeout(t1);
      window.clearTimeout(t2);
      cancelAnimationFrame(raf);
      io?.disconnect();
      for (const ro of ros) ro.disconnect();
    };
  }, []);

  const pxPerViewUnit = Math.max(svgClientW, 1) / W;
  const axisFontUser = AXIS_LABEL_SCREEN_PX / pxPerViewUnit;
  const hoverFontUser = HOVER_LABEL_SCREEN_PX / pxPerViewUnit;

  const layout = useMemo(() => {
    if (!series?.length) return null;
    const vals = series.map((p) => p.value);
    const minV = Math.min(...vals);
    const maxV = Math.max(...vals);
    const spread = maxV - minV || 1;
    const n = series.length;
    const innerW = W - PAD_L - PAD_R;
    const innerH = H - PAD_T - PAD_B;
    const yBase = PAD_T + innerH;
    const pts = series.map((p, i) => {
      const x = PAD_L + (n <= 1 ? innerW / 2 : (i / (n - 1)) * innerW);
      const y = PAD_T + innerH - ((p.value - minV) / spread) * innerH;
      return [x, y] as const;
    });
    const smoothTop = smoothPathThrough(pts);
    const firstX = pts[0][0];
    const firstY = pts[0][1];
    const lastX = pts[n - 1][0];
    const lastY = pts[n - 1][1];
    const cornerR = Math.min(10, innerH * 0.2, (lastX - firstX) * 0.04);
    const areaD = buildAreaPath(
      smoothTop,
      firstX,
      firstY,
      lastX,
      lastY,
      yBase,
      cornerR,
    );
    const yTicks = [maxV, minV].map((v) => Math.round(v * 100) / 100);
    const xTicks = monthStartTickIndices(series).map(({ i, label }) => ({
      label,
      x: pts[i][0],
    }));
    return {
      minV,
      spread,
      innerH,
      yBase,
      pts,
      smoothTop,
      areaD,
      yTicks,
      xTicks,
    };
  }, [series]);

  const pickIndex = useCallback(
    (svgX: number, l: NonNullable<typeof layout>) => {
      let best = 0;
      let bestD = Infinity;
      for (let i = 0; i < l.pts.length; i++) {
        const d = Math.abs(l.pts[i][0] - svgX);
        if (d < bestD) {
          bestD = d;
          best = i;
        }
      }
      return best;
    },
    [],
  );

  const onPointerMove = useCallback(
    (e: PointerEvent<SVGSVGElement>) => {
      if (!layout) return;
      const rect = e.currentTarget.getBoundingClientRect();
      const svgX = ((e.clientX - rect.left) / rect.width) * W;
      const svgY = ((e.clientY - rect.top) / rect.height) * H;
      if (svgX < PAD_L || svgX > W - PAD_R || svgY < PAD_T || svgY > H - PAD_B) {
        setHoverI(null);
        return;
      }
      setHoverI(pickIndex(svgX, layout));
    },
    [layout, pickIndex],
  );

  const onPointerLeave = useCallback(() => setHoverI(null), []);

  const activeI = hoverI ?? keyboardI;

  const onChartKeyDown = useCallback(
    (e: KeyboardEvent<HTMLDivElement>) => {
      if (!series?.length) return;
      if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
        e.preventDefault();
        setKeyboardI((prev) => {
          const start = prev ?? 0;
          if (e.key === "ArrowLeft") {
            return Math.max(0, start - 1);
          }
          return Math.min(series.length - 1, start + 1);
        });
      }
    },
    [series],
  );

  useEffect(() => {
    if (viewMode === "chart" && keyboardI == null && series?.length) {
      setKeyboardI(series.length - 1);
    }
  }, [viewMode, keyboardI, series]);

  if (loading) {
    return (
      <div className="space-y-2" role="status">
        <DashboardChartSkeleton />
        <p className="app-text-muted">Fetching VIX history…</p>
      </div>
    );
  }

  if (!series?.length) {
    return <p className="app-text-muted">No VIX history available.</p>;
  }

  if (!layout) return null;

  const { minV, spread, innerH, pts, smoothTop, areaD, yTicks, yBase, xTicks } =
    layout;
  const hi = activeI;

  let hoverLabel: { x: number; y: number; date: string; val: string } | null =
    null;
  if (hi != null && series[hi]) {
    const [px, py] = pts[hi];
    const date = formatChartDate(series[hi].date);
    const val = series[hi].value.toFixed(2);
    const boxW = 92;
    const boxH = 36;
    let lx = px - boxW / 2;
    let ly = py - boxH - 6;
    lx = Math.min(Math.max(lx, PAD_L + 2), W - PAD_R - boxW - 2);
    if (ly < PAD_T + 2) ly = py + 8;
    ly = Math.min(ly, H - PAD_B - boxH - 2);
    hoverLabel = { x: lx, y: ly, date, val };
  }

  const liveAnnouncement =
    hi != null && series[hi]
      ? `${formatChartDate(series[hi].date)}, VIX ${series[hi].value.toFixed(2)}`
      : "";

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-end gap-1">
        <button
          type="button"
          className={[
            "rounded-md px-2.5 py-1 text-xs font-medium transition",
            viewMode === "chart"
              ? "bg-sky-100 text-sky-900 dark:bg-sky-950/60 dark:text-sky-100"
              : "text-zinc-600 hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-zinc-800",
          ].join(" ")}
          aria-pressed={viewMode === "chart"}
          onClick={() => setViewMode("chart")}
        >
          Chart
        </button>
        <button
          type="button"
          className={[
            "rounded-md px-2.5 py-1 text-xs font-medium transition",
            viewMode === "table"
              ? "bg-sky-100 text-sky-900 dark:bg-sky-950/60 dark:text-sky-100"
              : "text-zinc-600 hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-zinc-800",
          ].join(" ")}
          aria-pressed={viewMode === "table"}
          onClick={() => setViewMode("table")}
        >
          Table
        </button>
      </div>

      {viewMode === "table" ? (
        <div className="overflow-x-auto rounded-md border border-zinc-200 dark:border-zinc-800">
          <table className="min-w-full text-left text-sm">
            <thead className="border-b border-zinc-200 bg-zinc-50 text-xs uppercase tracking-wide text-zinc-500 dark:border-zinc-800 dark:bg-zinc-900/80 dark:text-zinc-400">
              <tr>
                <th scope="col" className="px-3 py-2 font-medium">
                  Date
                </th>
                <th scope="col" className="px-3 py-2 font-medium">
                  VIX
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-100 dark:divide-zinc-800">
              {series.map((row) => (
                <tr key={row.date}>
                  <td className="px-3 py-1.5 tabular-nums text-zinc-800 dark:text-zinc-200">
                    {formatChartDate(row.date)}
                  </td>
                  <td className="px-3 py-1.5 tabular-nums text-zinc-800 dark:text-zinc-200">
                    {row.value.toFixed(2)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
      <div
        ref={containerRef}
        className="w-full min-w-0 outline-none focus-visible:ring-2 focus-visible:ring-sky-500/40"
        tabIndex={0}
        role="group"
        aria-label="India VIX last three months. Use left and right arrow keys to move through dates."
        onKeyDown={onChartKeyDown}
      >
        <p className="sr-only" aria-live="polite">
          {liveAnnouncement}
        </p>
        <svg
          ref={svgRef}
          viewBox={`0 0 ${W} ${H}`}
          className="block w-full min-w-0 cursor-crosshair touch-none overflow-visible"
          style={{ aspectRatio: `${W} / ${H}` }}
          role="img"
          aria-label="India VIX line chart"
          onPointerMove={onPointerMove}
          onPointerLeave={onPointerLeave}
          onPointerCancel={onPointerLeave}
        >
          <defs>
            <linearGradient
              id={`vix-area-${gradId}`}
              gradientUnits="userSpaceOnUse"
              x1={0}
              y1={PAD_T}
              x2={0}
              y2={yBase}
            >
              <stop
                offset="0%"
                stopColor={LINE_BLUE}
                stopOpacity={AREA_TOP_OPACITY}
              />
              <stop
                offset="100%"
                stopColor={LINE_BLUE}
                stopOpacity={AREA_BOTTOM_OPACITY}
              />
            </linearGradient>
          </defs>
          {yTicks.map((tick) => {
            const y = PAD_T + innerH - ((tick - minV) / spread) * innerH;
            return (
              <g key={tick}>
                <line
                  x1={PAD_L}
                  y1={y}
                  x2={W - PAD_R}
                  y2={y}
                  className="stroke-zinc-200 dark:stroke-zinc-700/90"
                  strokeWidth={0.45}
                />
                <text
                  x={PAD_L - 5}
                  y={y}
                  textAnchor="end"
                  dominantBaseline="middle"
                  className="fill-zinc-500 font-sans dark:fill-zinc-500"
                  fontSize={axisFontUser}
                >
                  {tick.toFixed(1)}
                </text>
              </g>
            );
          })}
          <path
            d={areaD}
            fill={`url(#vix-area-${gradId})`}
            stroke="none"
          />
          <path
            d={smoothTop}
            fill="none"
            stroke={LINE_BLUE}
            strokeWidth={LINE_WIDTH}
            strokeLinecap="round"
            strokeLinejoin="round"
          />
          {hi != null ? (
            <>
              <circle
                cx={pts[hi][0]}
                cy={pts[hi][1]}
                r={2.75}
                fill={LINE_BLUE}
                stroke="white"
                strokeWidth={0.75}
                className="dark:stroke-zinc-900"
              />
              <line
                x1={pts[hi][0]}
                y1={PAD_T}
                x2={pts[hi][0]}
                y2={H - PAD_B}
                stroke={LINE_BLUE}
                strokeOpacity={0.35}
                strokeWidth={0.5}
                strokeDasharray="3 2"
                pointerEvents="none"
              />
            </>
          ) : null}
          {hoverLabel ? (
            <g pointerEvents="none">
              <rect
                x={hoverLabel.x}
                y={hoverLabel.y}
                width={92}
                height={36}
                rx={4}
                className="fill-zinc-900/90 stroke-zinc-600/35 dark:fill-zinc-950/94 dark:stroke-zinc-500/35"
                strokeWidth={0.5}
              />
              <text
                x={hoverLabel.x + 46}
                y={hoverLabel.y + 15}
                textAnchor="middle"
                dominantBaseline="middle"
                fill="rgb(244 244 245)"
                className="font-sans"
                fontSize={hoverFontUser}
                fontWeight={500}
              >
                {hoverLabel.date}
              </text>
              <text
                x={hoverLabel.x + 46}
                y={hoverLabel.y + 29}
                textAnchor="middle"
                dominantBaseline="middle"
                fill={LINE_BLUE}
                className="font-mono"
                fontSize={hoverFontUser}
                fontWeight={600}
              >
                {`VIX ${hoverLabel.val}`}
              </text>
            </g>
          ) : null}
          {xTicks.map(({ x, label }) => (
            <text
              key={label + x}
              x={Math.min(Math.max(x, PAD_L + 14), W - 14)}
              y={H - 6}
              textAnchor="middle"
              dominantBaseline="auto"
              className="fill-zinc-600 font-sans dark:fill-zinc-500"
              fontSize={axisFontUser}
            >
              {label}
            </text>
          ))}
        </svg>
      </div>
      )}

      <p className="app-text-muted">
        India VIX (INDVIX), daily close — last ~3 months
        {viewMode === "chart"
          ? " · use arrow keys on the chart or switch to Table view"
          : null}
      </p>
    </div>
  );
}

function formatChartDate(iso: string): string {
  const d = iso.slice(0, 10);
  const [y, m, day] = d.split("-");
  if (!y || !m || !day) return iso;
  const months = [
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
  ];
  const mi = parseInt(m, 10) - 1;
  const label = months[mi] ?? m;
  return `${day} ${label} ${y}`;
}
