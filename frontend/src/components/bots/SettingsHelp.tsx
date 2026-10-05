"use client";

/** The explanatory furniture every bot's settings tabs share.
 *
 *  A label like "Level 1 locks" or "Net Δ band per lot" means nothing to someone who has not
 *  read the strategy's design, so every tab opens with what it controls and, where settings
 *  combine, closes with a worked example computed from the values on screen. One look for all
 *  six bots, so a reader learns where to find the explanation once.
 */

export function TabIntro({ children }: { children: React.ReactNode }) {
  return <div className="app-card-muted space-y-2 p-3 text-hint">{children}</div>;
}

export function WorkedExample({
  title,
  children,
  footer,
}: {
  title: React.ReactNode;
  children: React.ReactNode;
  footer?: React.ReactNode;
}) {
  return (
    <div className="app-card-muted p-3 text-hint">
      <p className="font-semibold text-text">{title}</p>
      <ul className="mt-2 list-disc space-y-1 pl-4">{children}</ul>
      {footer ? <p className="mt-2">{footer}</p> : null}
    </div>
  );
}

/** A refusal the backend would make on Save, said beside the fields instead. */
export function SettingsError({ children }: { children: React.ReactNode }) {
  return (
    <p role="alert" className="text-hint text-down">
      {children}
    </p>
  );
}

export function rupees(n: number, digits = 2): string {
  return `₹${n.toLocaleString("en-IN", { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
}

/** Trims float noise ("0.30000000000000004") without forcing trailing zeros. */
export function num(n: number, digits = 2): string {
  return n.toLocaleString("en-IN", { maximumFractionDigits: digits });
}
