"use client";

import { useEffect, useState } from "react";
import { snapQuantityToLotMultiple } from "@/lib/strategy-builder/leg-ui-helpers";

export function LegQuantityInput({
  legId,
  lots,
  lotSize,
  onLotsChange,
  className,
}: {
  legId: string;
  lots: number;
  lotSize: number;
  onLotsChange: (newLots: number) => void;
  className: string;
}) {
  const ls = lotSize > 0 ? lotSize : 1;
  const [text, setText] = useState(() =>
    Number.isFinite(lots) && lots > 0 ? String(Math.round(lots * ls)) : "",
  );

  useEffect(() => {
    const next =
      Number.isFinite(lots) && lots > 0 ? String(Math.round(lots * ls)) : "";
    const timer = setTimeout(() => setText(next), 0);
    return () => clearTimeout(timer);
  }, [legId, lots, ls]);

  return (
    <input
      type="text"
      inputMode="numeric"
      autoComplete="off"
      aria-label="Quantity"
      className={className}
      value={text}
      onChange={(e) => {
        const t = e.target.value.replace(/,/g, "");
        if (t === "" || /^\d+$/.test(t)) setText(t);
      }}
      onBlur={() => {
        const t = text.replace(/,/g, "").trim();
        if (t === "") {
          onLotsChange(0);
          setText("");
          return;
        }
        const raw = parseInt(t, 10);
        if (!Number.isFinite(raw) || raw <= 0) {
          onLotsChange(0);
          setText("");
          return;
        }
        const snapped = snapQuantityToLotMultiple(raw, ls);
        const newLots = Math.max(1, Math.round(snapped / ls));
        onLotsChange(newLots);
        setText(String(newLots * ls));
      }}
    />
  );
}
