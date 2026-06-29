"use client";

import { InfoPopover } from "@/components/strategy-builder/InfoPopover";

export function LegQuantityHeader() {
  return (
    <th className="px-2 py-1.5 font-medium">
      <span className="inline-flex items-center gap-1">
        Quantity
        <InfoPopover title="Quantity rounding" ariaLabel="Quantity rounding help">
          Quantity is rounded to the nearest lot-size multiple automatically (up or
          down) when you finish editing.
        </InfoPopover>
      </span>
    </th>
  );
}
