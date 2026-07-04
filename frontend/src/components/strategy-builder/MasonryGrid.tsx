"use client";

import type { ReactNode } from "react";
import Masonry from "react-masonry-css";

/** Max 3 columns — tiles share column width (widest content sets column size). */
const BREAKPOINT_COLS = {
  default: 3,
  1024: 2,
  640: 1,
};

const GAP_CLASS: Record<string, string> = {
  "gap-2": "masonry-grid--gap-2",
  "gap-3": "masonry-grid--gap-3",
  "gap-4": "masonry-grid--gap-4",
  "gap-5": "masonry-grid--gap-5",
  "gap-6": "masonry-grid--gap-6",
};

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
  const gapModifier = GAP_CLASS[gapClassName] ?? GAP_CLASS["gap-4"];

  return (
    <Masonry
      breakpointCols={BREAKPOINT_COLS}
      className={`masonry-grid ${gapModifier} ${className}`.trim()}
      columnClassName={`masonry-grid_column ${gapModifier}`}
    >
      {items.map((itemData, index) => (
        <div key={getKey(itemData, index)} className="w-full">
          {renderItem(itemData, index)}
        </div>
      ))}
    </Masonry>
  );
}
