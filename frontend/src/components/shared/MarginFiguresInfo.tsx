"use client";

import { InfoPopover } from "@/components/ui/InfoPopover";

/**
 * Explains why the app's blocked-margin figures — the Dashboard "Margin used"
 * (ICICI's reported utilisation) and the Portfolio "SPAN + ELM" (our netted
 * computation) — can occasionally diverge: ICICI's margin API does not always
 * report the amount upstreamed to the exchange, so the reported margin may not
 * reconcile exactly across screens or with ICICI's own portal.
 */
export function MarginFiguresInfoTrigger() {
  return (
    <InfoPopover
      title="Margin figures may differ"
      ariaLabel="Why margin figures may differ"
    >
      <div className="space-y-2">
        <p>
          ICICI&apos;s margin API doesn&apos;t always report the amount
          upstreamed to the exchange. When that value is missing, the margin
          shown here can diverge slightly from ICICI&apos;s own view.
        </p>
        <p className="text-muted">
          Because of this, the blocked-margin figures on the Dashboard and
          Portfolio may not always match exactly.
        </p>
      </div>
    </InfoPopover>
  );
}
