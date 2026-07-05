"use client";

function ResetIcon({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      width="12"
      height="12"
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
  );
}

function ScenarioSlider({
  label,
  valueLabel,
  min,
  max,
  value,
  onChange,
  disabled,
  ariaLabel,
}: {
  label: string;
  valueLabel: string;
  min: number;
  max: number;
  value: number;
  onChange: (v: number) => void;
  disabled?: boolean;
  ariaLabel: string;
}) {
  const pct = max > min ? ((value - min) / (max - min)) * 100 : 0;
  return (
    <label className="block min-w-0">
      <div className="mb-1 flex items-center justify-between gap-2 text-[11px] font-medium text-muted">
        <span>{label}</span>
        <span className="font-mono tabular-nums text-foreground">
          {valueLabel}
        </span>
      </div>
      {/*
        No vertical padding here: the decorative rail divs are centered via
        top-1/2 against *this* wrapper's own box, so the wrapper's rendered
        height must exactly match the <input>'s (block kills the native
        inline-replaced baseline gap) — otherwise the rail centers a few px
        away from where the browser actually draws the thumb.
      */}
      <div className="relative">
        <div className="pointer-events-none absolute top-1/2 left-0 h-1 w-full -translate-y-1/2 rounded-full bg-track" />
        <div
          className="pointer-events-none absolute top-1/2 left-0 h-1 -translate-y-1/2 rounded-full bg-accent-strong"
          style={{ width: `${pct}%` }}
        />
        <input
          type="range"
          min={min}
          max={max}
          step={1}
          value={value}
          disabled={disabled}
          onChange={(e) => onChange(Number(e.target.value))}
          className="sb-range-slim sb-range-xs relative z-0 block w-full"
          aria-label={ariaLabel}
        />
      </div>
    </label>
  );
}

type Props = {
  /** Days-to-expiry currently modeled for the T+0 curve (0 = fully decayed, morphs onto expiry). */
  dteDays: number;
  /** Live/actual DTE for this expiry — the slider's upper bound and the "reset" target. */
  liveDteDays: number;
  onDteChange: (v: number) => void;
  /** IV shock applied on top of chain IV, in percent (±10). */
  ivShockPct: number;
  onIvShockChange: (v: number) => void;
  onReset: () => void;
  disabled?: boolean;
};

const IV_SHOCK_MIN = -10;
const IV_SHOCK_MAX = 10;

/** "What-if" DTE + IV shock sliders that drive the T+0 curve in PortfolioGroupPayoffPanel. */
export function PayoffScenarioControls({
  dteDays,
  liveDteDays,
  onDteChange,
  ivShockPct,
  onIvShockChange,
  onReset,
  disabled = false,
}: Props) {
  const isLive = dteDays === liveDteDays && ivShockPct === 0;
  const dteMax = Math.max(1, liveDteDays);

  return (
    <div className="mt-3 rounded-[9px] border border-border-soft bg-panel2/60 p-2.5">
      <div className="mb-2 flex items-center justify-between">
        <span className="text-[11px] font-semibold uppercase tracking-wide text-faint">
          What-if: T+0 scenario
        </span>
        <button
          type="button"
          onClick={onReset}
          disabled={disabled || isLive}
          title="Reset to live DTE and chain IV"
          className="inline-flex items-center gap-1 text-[11px] font-medium text-muted transition-colors hover:text-foreground disabled:pointer-events-none disabled:opacity-40"
        >
          <ResetIcon />
          Live
        </button>
      </div>
      <div className="grid grid-cols-1 gap-x-4 gap-y-3 sm:grid-cols-2">
        <ScenarioSlider
          label="Days to expiry"
          valueLabel={`${dteDays}d`}
          min={0}
          max={dteMax}
          value={dteDays}
          onChange={onDteChange}
          disabled={disabled}
          ariaLabel="Days to expiry"
        />
        <ScenarioSlider
          label="IV shock"
          valueLabel={ivShockPct > 0 ? `+${ivShockPct}%` : `${ivShockPct}%`}
          min={IV_SHOCK_MIN}
          max={IV_SHOCK_MAX}
          value={ivShockPct}
          onChange={onIvShockChange}
          disabled={disabled}
          ariaLabel="Implied volatility shock percent"
        />
      </div>
    </div>
  );
}
