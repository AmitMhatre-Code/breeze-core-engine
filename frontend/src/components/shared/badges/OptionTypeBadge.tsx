"use client";

/**
 * Canonical outline CE/PE badge for a leg's option type — same look
 * everywhere it's used (Portfolio, Orders, Place Order, confirmation
 * modals, Basket Order / Strategy Builder legs). Pass `onToggle` to make
 * it an interactive flip control (Place Order's type selector and the
 * legs tables); omit it for a static descriptor badge everywhere else.
 */
export function OptionTypeBadge({
  right,
  onToggle,
  disabled,
  className,
}: {
  right: unknown;
  onToggle?: (next: "Call" | "Put") => void;
  disabled?: boolean;
  className?: string;
}) {
  const r = String(right ?? "").trim();
  if (r !== "Call" && r !== "Put") return null;
  const isPut = r === "Put";
  const label = isPut ? "PE" : "CE";
  const style = {
    borderColor: isPut ? "var(--down)" : "var(--up)",
    color: isPut ? "var(--down)" : "var(--up)",
  };

  if (!onToggle) {
    return (
      <span
        className={className ?? "inline-flex items-center justify-center rounded-md border px-2 py-0.5 text-button"}
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
      aria-pressed={!isPut}
      title="Click to flip Call/Put"
      aria-label={
        isPut
          ? "Put (PE). Click to switch to Call."
          : "Call (CE). Click to switch to Put."
      }
      onClick={() => onToggle(isPut ? "Call" : "Put")}
      className={
        className ??
        "inline-flex items-center justify-center rounded-md border px-2 py-0.5 text-button transition hover:brightness-110 focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/40 disabled:pointer-events-none disabled:opacity-50"
      }
      style={style}
    >
      {label}
    </button>
  );
}
