"use client";

import type { PointerEvent } from "react";
import { useCallback, useMemo, useState } from "react";

type Point = { date: string; value: number };

const W = 560;
const H = 140;
const PAD_L = 40;
const PAD_R = 10;
const PAD_T = 8;
const PAD_B = 20;

/** Line & markers */
const LINE_BLUE = "#3b82f6";
const LINE_WIDTH = 1;
const DOT_R = 1.75;
const DOT_HOVER_R = 3;

export function Vix30dChart({ series }: { series: Point[] }) {
  const [hoverI, setHoverI] = useState<number | null>(null);

  const layout = useMemo(() => {
    if (!series?.length) return null;
    const vals = series.map((p) => p.value);
    const minV = Math.min(...vals);
    const maxV = Math.max(...vals);
    const spread = maxV - minV || 1;
    const n = series.length;
    const innerW = W - PAD_L - PAD_R;
    const innerH = H - PAD_T - PAD_B;
    const pts = series.map((p, i) => {
      const x = PAD_L + (n <= 1 ? innerW / 2 : (i / (n - 1)) * innerW);
      const y = PAD_T + innerH - ((p.value - minV) / spread) * innerH;
      return [x, y] as const;
    });
    const pathD = pts
      .map(([x, y], i) => `${i === 0 ? "M" : "L"} ${x} ${y}`)
      .join(" ");
    const yTicks = [maxV, minV + spread / 2, minV].map(
      (v) => Math.round(v * 100) / 100,
    );
    const first = series[0]?.date?.slice(5) ?? "";
    const last = series[n - 1]?.date?.slice(5) ?? "";
    return {
      minV,
      spread,
      innerH,
      pts,
      pathD,
      yTicks,
      first,
      last,
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
      if (
        svgX < PAD_L ||
        svgX > W - PAD_R ||
        svgY < PAD_T ||
        svgY > H - PAD_B
      ) {
        setHoverI(null);
        return;
      }
      setHoverI(pickIndex(svgX, layout));
    },
    [layout, pickIndex],
  );

  const onPointerLeave = useCallback(() => setHoverI(null), []);

  if (!series?.length) {
    return <p className="app-text-muted">No VIX history available.</p>;
  }

  if (!layout) return null;

  const { minV, spread, innerH, pts, pathD, yTicks, first, last } = layout;
  const hi = hoverI;

  let hoverLabel: { x: number; y: number; date: string; val: string } | null =
    null;
  if (hi != null && series[hi]) {
    const [px, py] = pts[hi];
    const date = formatChartDate(series[hi].date);
    const val = series[hi].value.toFixed(2);
    const boxW = 76;
    const boxH = 26;
    let lx = px - boxW / 2;
    let ly = py - boxH - 6;
    lx = Math.min(Math.max(lx, PAD_L + 2), W - PAD_R - boxW - 2);
    if (ly < PAD_T + 2) ly = py + 8;
    ly = Math.min(ly, H - PAD_B - boxH - 2);
    hoverLabel = { x: lx, y: ly, date, val };
  }

  return (
    <div className="space-y-2">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full max-h-44 cursor-crosshair touch-none"
        role="img"
        aria-label="India VIX last 30 trading days; hover for date and value"
        onPointerMove={onPointerMove}
        onPointerLeave={onPointerLeave}
        onPointerCancel={onPointerLeave}
      >
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
                strokeWidth={0.6}
              />
              <text
                x={PAD_L - 5}
                y={y + 3}
                textAnchor="end"
                className="fill-zinc-500 dark:fill-zinc-500"
                style={{ fontSize: "6.5px" }}
              >
                {tick.toFixed(1)}
              </text>
            </g>
          );
        })}
        <path
          d={pathD}
          fill="none"
          stroke={LINE_BLUE}
          strokeWidth={LINE_WIDTH}
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        {pts.map(([x, y], i) => (
          <circle
            key={i}
            cx={x}
            cy={y}
            r={hi === i ? DOT_HOVER_R : DOT_R}
            fill={LINE_BLUE}
            stroke="white"
            strokeWidth={hi === i ? 0.9 : 0.5}
            className="dark:stroke-zinc-900"
          />
        ))}
        {hi != null ? (
          <line
            x1={pts[hi][0]}
            y1={PAD_T}
            x2={pts[hi][0]}
            y2={H - PAD_B}
            stroke={LINE_BLUE}
            strokeOpacity={0.35}
            strokeWidth={0.65}
            strokeDasharray="3 2"
            pointerEvents="none"
          />
        ) : null}
        {hoverLabel ? (
          <g pointerEvents="none">
            <rect
              x={hoverLabel.x}
              y={hoverLabel.y}
              width={76}
              height={26}
              rx={3}
              className="fill-zinc-900/90 stroke-zinc-600/35 dark:fill-zinc-950/94 dark:stroke-zinc-500/35"
              strokeWidth={0.5}
            />
            <text
              x={hoverLabel.x + 38}
              y={hoverLabel.y + 11}
              textAnchor="middle"
              fill="rgb(244 244 245)"
              style={{ fontSize: "6.5px", fontWeight: 500 }}
            >
              {hoverLabel.date}
            </text>
            <text
              x={hoverLabel.x + 38}
              y={hoverLabel.y + 21}
              textAnchor="middle"
              fill={LINE_BLUE}
              style={{ fontSize: "7px", fontWeight: 600, fontFamily: "ui-monospace, monospace" }}
            >
              {`VIX ${hoverLabel.val}`}
            </text>
          </g>
        ) : null}
        <text
          x={PAD_L}
          y={H - 3}
          className="fill-zinc-600 dark:fill-zinc-500"
          style={{ fontSize: "6.5px" }}
        >
          {first}
        </text>
        <text
          x={W - PAD_R}
          y={H - 3}
          textAnchor="end"
          className="fill-zinc-600 dark:fill-zinc-500"
          style={{ fontSize: "6.5px" }}
        >
          {last}
        </text>
      </svg>
      <p className="app-text-muted">
        India VIX (INDVIX), daily close — last ~30 days · hover for date
        &amp; value on chart
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
