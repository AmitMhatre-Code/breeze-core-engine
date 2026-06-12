"use client";

import type { ReactNode } from "react";

const GAP_STYLES: Record<string, { column: string; item: string }> = {
  "gap-2": { column: "gap-x-2", item: "mb-2" },
  "gap-3": { column: "gap-x-3", item: "mb-3" },
  "gap-4": { column: "gap-x-4", item: "mb-4" },
  "gap-5": { column: "gap-x-5", item: "mb-5" },
  "gap-6": { column: "gap-x-6", item: "mb-6" },
};

export function MasonryGrid<T>({
  items,
  className = "",
  gapClassName = "gap-3",
  getKey,
  renderItem,
}: {
  items: T[];
  className?: string;
  gapClassName?: string;
  getKey: (item: T, index: number) => string | number;
  renderItem: (item: T, index: number) => ReactNode;
}) {
  const { column, item } = GAP_STYLES[gapClassName] ?? GAP_STYLES["gap-3"];

  return (
    <div
      className={`columns-1 sm:columns-2 lg:columns-3 xl:columns-4 ${column} ${className}`}
    >
      {items.map((itemData, index) => (
        <div
          key={getKey(itemData, index)}
          className={`w-full min-w-0 break-inside-avoid ${item}`}
        >
          {renderItem(itemData, index)}
        </div>
      ))}
    </div>
  );
}
