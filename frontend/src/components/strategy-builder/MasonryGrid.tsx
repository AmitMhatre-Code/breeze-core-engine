"use client";

import type { ReactNode } from "react";

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
  return (
    <div
      className={`columns-1 sm:columns-2 lg:columns-3 xl:columns-4 ${gapClassName} ${className}`}
    >
      {items.map((item, index) => (
        <div
          key={getKey(item, index)}
          className={`mb-3 break-inside-avoid ${gapClassName}`}
        >
          {renderItem(item, index)}
        </div>
      ))}
    </div>
  );
}
