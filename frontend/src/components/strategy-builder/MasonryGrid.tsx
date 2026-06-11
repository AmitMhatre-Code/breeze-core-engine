"use client";

import { Fragment, useMemo, useSyncExternalStore, type ReactNode } from "react";

function subscribeBreakpoint(query: string, callback: () => void) {
  const mq = window.matchMedia(query);
  mq.addEventListener("change", callback);
  return () => mq.removeEventListener("change", callback);
}

function columnCountForBreakpoint(query: string, columns: number): number {
  return window.matchMedia(query).matches ? columns : 1;
}

function distributeItems<T>(items: T[], columns: number): T[][] {
  const cols: T[][] = Array.from({ length: columns }, () => []);
  items.forEach((item, index) => {
    cols[index % columns].push(item);
  });
  return cols;
}

export function MasonryGrid<T>({
  items,
  columns = 2,
  breakpoint = "(min-width: 640px)",
  className = "",
  columnClassName = "",
  gapClassName = "gap-3",
  getKey,
  renderItem,
}: {
  items: T[];
  columns?: number;
  breakpoint?: string;
  className?: string;
  columnClassName?: string;
  gapClassName?: string;
  getKey: (item: T, index: number) => string | number;
  renderItem: (item: T, index: number) => ReactNode;
}) {
  const columnCount = useSyncExternalStore(
    (callback) => subscribeBreakpoint(breakpoint, callback),
    () => columnCountForBreakpoint(breakpoint, columns),
    () => 1,
  );

  const distributed = useMemo(
    () => distributeItems(items, columnCount),
    [items, columnCount],
  );

  return (
    <div className={`flex ${gapClassName} ${className}`}>
      {distributed.map((colItems, colIndex) => (
        <div
          key={colIndex}
          className={`flex min-w-0 flex-1 flex-col ${gapClassName} ${columnClassName}`}
        >
          {colItems.map((item, rowIndex) => {
            const itemIndex = rowIndex * columnCount + colIndex;
            return (
              <Fragment key={getKey(item, itemIndex)}>
                {renderItem(item, itemIndex)}
              </Fragment>
            );
          })}
        </div>
      ))}
    </div>
  );
}
