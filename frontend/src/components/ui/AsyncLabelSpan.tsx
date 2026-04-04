import type { ReactNode } from "react";

/**
 * Keeps a constant button (or control) size while switching between idle and busy labels,
 * avoiding layout shift and peek-through from sibling or backdrop layers.
 */
export function AsyncLabelSpan({
  busy,
  idleLabel,
  busyLabel,
  className,
}: {
  busy: boolean;
  idleLabel: ReactNode;
  busyLabel: ReactNode;
  className?: string;
}) {
  return (
    <span
      className={[
        "inline-grid grid-cols-1 grid-rows-1 place-items-center",
        className,
      ]
        .filter(Boolean)
        .join(" ")}
    >
      <span className="invisible col-start-1 row-start-1" aria-hidden>
        {idleLabel}
      </span>
      <span className="invisible col-start-1 row-start-1" aria-hidden>
        {busyLabel}
      </span>
      <span className="col-start-1 row-start-1">
        {busy ? busyLabel : idleLabel}
      </span>
    </span>
  );
}
