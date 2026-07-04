"use client";

export function IvShockSlider({
  value,
  onChange,
}: {
  value: number;
  onChange: (v: number) => void;
}) {
  const min = -20;
  const max = 20;
  const pct = ((value - min) / (max - min)) * 100;
  const badgeText = value === 0 ? "0%" : `${value >= 0 ? "+" : ""}${value}%`;

  return (
    <div className="app-card-muted min-w-0 p-4">
      <div className="mb-3 flex flex-wrap items-end justify-between gap-2">
        <div>
          <div className="text-xs font-semibold text-foreground">
            Implied volatility shock
          </div>
          <p className="mt-0.5 max-w-xl text-[13px] leading-relaxed text-muted">
            Adjust modeled IV for the chart and Greeks. Range −20% to +20%
            relative to chain IV.
          </p>
        </div>
        <button
          type="button"
          className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border border-border bg-panel text-muted transition hover:border-accent/40 hover:bg-panel2 focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/40 disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50"
          onClick={() => onChange(0)}
          disabled={value === 0}
          aria-label="Reset IV shock"
          title="Reset IV shock"
        >
          <svg
            className="h-3.5 w-3.5"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden
          >
            <path d="M3 12a9 9 0 1 0 2.64-6.36" />
            <path d="M3 3v6h6" />
          </svg>
        </button>
      </div>
      <div className="relative pt-7">
        <div
          className="pointer-events-none absolute top-0 z-10 -translate-x-1/2 whitespace-nowrap rounded-full border border-accent/30 bg-accent-tint px-2.5 py-1 font-mono text-xs font-semibold tabular-nums text-accent-strong"
          style={{ left: `clamp(1.25rem, ${pct}%, calc(100% - 1.25rem))` }}
          aria-hidden
        >
          {badgeText}
        </div>
        <div className="pointer-events-none absolute top-1/2 left-0 h-1.5 w-full -translate-y-1/2 rounded-full bg-track" />
        <div
          className="pointer-events-none absolute top-1/2 left-0 h-1.5 -translate-y-1/2 rounded-full bg-accent-strong"
          style={{ width: `${pct}%` }}
          aria-hidden
        />
        <input
          type="range"
          min={min}
          max={max}
          value={value}
          onChange={(e) => onChange(Number(e.target.value))}
          className="sb-range-slim relative z-0 w-full"
          aria-label="IV shock percent"
          aria-valuetext={`${value >= 0 ? "+" : ""}${value} percent`}
        />
        <div className="mt-2 flex justify-between font-mono text-[12px] font-medium tabular-nums text-faint">
          <span>−20%</span>
          <span className="text-muted">0</span>
          <span>+20%</span>
        </div>
      </div>
    </div>
  );
}
