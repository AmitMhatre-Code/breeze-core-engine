"use client";

export type ExchangeSegment = "NFO" | "BFO";

const NSE_CLASSES = "border-accent/60 bg-accent-tint text-accent-strong";
const BSE_CLASSES = "border-amber-accent/60 bg-amber-tint text-amber-on-tint";

function ExchangeFlipIcon() {
  return (
    <svg
      width="11"
      height="11"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="M7 3v13M7 16l-3.5-3.5M7 16l3.5-3.5M17 21V8M17 8l-3.5 3.5M17 8l3.5 3.5" />
    </svg>
  );
}

/**
 * Single-button flip switch for the NFO/BFO exchange segment, mirroring the
 * Call/Put flip-switch pattern (click to flip, not a two-button segmented
 * picker). Labels display as NSE/BSE — the underlying value stays NFO/BFO
 * since that's what the backend expects.
 */
export function ExchangeFlipToggle({
  value,
  onChange,
  disabled,
  className,
}: {
  value: ExchangeSegment;
  onChange: (segment: ExchangeSegment) => void;
  disabled?: boolean;
  className?: string;
}) {
  const isNse = value === "NFO";
  return (
    <button
      type="button"
      disabled={disabled}
      title="Click to flip NSE/BSE"
      aria-label={
        isNse
          ? "Exchange: NSE. Click to switch to BSE."
          : "Exchange: BSE. Click to switch to NSE."
      }
      onClick={() => onChange(isNse ? "BFO" : "NFO")}
      className={[
        "flex h-10 items-center justify-center gap-1.5 rounded-[9px] border-[1.5px] px-3.5 font-mono text-table font-bold uppercase tracking-[.04em] transition disabled:pointer-events-none disabled:opacity-50",
        isNse ? NSE_CLASSES : BSE_CLASSES,
        className ?? "",
      ].join(" ")}
    >
      <ExchangeFlipIcon />
      {isNse ? "NSE" : "BSE"}
    </button>
  );
}
