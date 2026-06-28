"use client";

import { useEffect, useState } from "react";
import { snapQuantityToLotMultiple } from "@/lib/strategy-builder/leg-ui-helpers";

function applyDigitLimit(raw: string, maxDigits?: number): string {
  if (maxDigits == null || maxDigits <= 0) return raw;
  return raw.slice(0, maxDigits);
}

export function LegQuantityInput({
  legId,
  lots,
  lotSize,
  onLotsChange,
  className,
  maxDigits,
  snapWhileTyping = false,
}: {
  legId: string;
  lots: number;
  lotSize: number;
  onLotsChange: (newLots: number) => void;
  className: string;
  maxDigits?: number;
  snapWhileTyping?: boolean;
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

  const commitQuantity = (rawText: string, live: boolean) => {
    const t = applyDigitLimit(rawText.replace(/,/g, "").trim(), maxDigits);
    if (t === "") {
      if (!live) {
        onLotsChange(0);
        setText("");
      }
      return;
    }
    const raw = parseInt(t, 10);
    if (!Number.isFinite(raw) || raw <= 0) {
      if (!live) {
        onLotsChange(0);
        setText("");
      }
      return;
    }
    const snapped = snapQuantityToLotMultiple(raw, ls);
    const newLots = Math.max(1, Math.round(snapped / ls));
    onLotsChange(newLots);
    setText(String(newLots * ls));
  };

  return (
    <input
      type="text"
      inputMode="numeric"
      autoComplete="off"
      aria-label="Quantity"
      className={className}
      value={text}
      onChange={(e) => {
        const t = applyDigitLimit(e.target.value.replace(/,/g, ""), maxDigits);
        if (t === "" || /^\d+$/.test(t)) {
          setText(t);
          if (snapWhileTyping && t !== "") {
            commitQuantity(t, true);
          } else if (snapWhileTyping && t === "") {
            onLotsChange(0);
          }
        }
      }}
      onBlur={() => commitQuantity(text, false)}
    />
  );
}
