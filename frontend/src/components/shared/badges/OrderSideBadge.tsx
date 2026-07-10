"use client";

/**
 * Canonical outline Buy/Sell badge — same visual convention as
 * `OptionTypeBadge` (rounded-md border, font-mono, no fill): Buy in green,
 * Sell in red. Pass `onToggle` to make it an interactive flip control
 * (the Basket Order / Strategy Builder legs tables); omit it for a static
 * descriptor badge everywhere else (Portfolio, Orders, Place Order,
 * confirmation modals).
 */
export function OrderSideBadge({
  side,
  onToggle,
  disabled,
}: {
  side: unknown;
  onToggle?: (next: "Buy" | "Sell") => void;
  disabled?: boolean;
}) {
  const s = String(side ?? "").trim().toLowerCase();
  if (s !== "buy" && s !== "sell") {
    return <span className="text-muted">{String(side ?? "") || "—"}</span>;
  }
  const isBuy = s === "buy";
  const label = isBuy ? "Buy" : "Sell";
  const style = {
    borderColor: isBuy ? "var(--up)" : "var(--down)",
    color: isBuy ? "var(--up)" : "var(--down)",
  };

  if (!onToggle) {
    return (
      <span
        className="inline-flex items-center justify-center rounded-sm border px-2 py-0.5 text-button uppercase"
        style={style}
      >
        {label}
      </span>
    );
  }

  return (
    <button
      type="button"
      disabled={disabled}
      aria-pressed={isBuy}
      title="Click to flip Buy/Sell"
      aria-label={
        isBuy
          ? "Buy. Click to switch to Sell."
          : "Sell. Click to switch to Buy."
      }
      onClick={() => onToggle(isBuy ? "Sell" : "Buy")}
      className="inline-flex items-center justify-center rounded-sm border px-2 py-0.5 text-button transition hover:brightness-110 focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/40 disabled:pointer-events-none disabled:opacity-50 uppercase"
      style={style}
    >
      {label}
    </button>
  );
}
