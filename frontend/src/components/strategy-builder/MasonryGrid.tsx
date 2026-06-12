"use client";

import type { ReactNode } from "react";

/** Content-sized tiles in a wrapping row with uniform horizontal and vertical gaps. */
export function MasonryGrid<T>({
  items,
  className = "",
  gapClassName = "gap-4",
  getKey,
  renderItem,
}: {
  items: T[];
  className?: string;
  gapClassName?: string;
  getKey: (item: T, index: number) => string | number;
  renderItem: (item: T, index: number) => ReactNode;
}) {
  return (
    <div
      className={`flex flex-wrap items-start ${gapClassName} ${className}`.trim()}
    >
      {items.map((itemData, index) => (
        <div key={getKey(itemData, index)} className="w-fit max-w-full">
          {renderItem(itemData, index)}
        </div>
      ))}
    </div>
  );
}
