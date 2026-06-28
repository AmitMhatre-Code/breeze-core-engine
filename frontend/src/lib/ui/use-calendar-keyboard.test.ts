import { describe, expect, it } from "vitest";

/** Mirrors initialFocusIndex from use-calendar-keyboard.ts */
function initialFocusIndex(
  cells: (number | null)[],
  selectedDay: number | null,
  todayDay: number | null,
): number {
  if (selectedDay != null) {
    const idx = cells.findIndex((d) => d === selectedDay);
    if (idx >= 0) return idx;
  }
  if (todayDay != null) {
    const idx = cells.findIndex((d) => d === todayDay);
    if (idx >= 0) return idx;
  }
  return cells.findIndex((d) => d != null);
}

function moveFocusIndex(
  cells: (number | null)[],
  from: number,
  delta: number,
): number {
  if (cells.length === 0) return -1;
  let i = from;
  for (let step = 0; step < cells.length; step++) {
    i += delta;
    if (i < 0 || i >= cells.length) return from;
    if (cells[i] != null) return i;
  }
  return from;
}

describe("use-calendar-keyboard helpers", () => {
  const cells = [
    null,
    null,
    null,
    1,
    2,
    3,
    4,
    5,
    6,
    7,
    8,
    9,
    10,
  ] as (number | null)[];

  it("prefers selected day for initial focus", () => {
    expect(initialFocusIndex(cells, 5, 10)).toBe(7);
  });

  it("falls back to today when no selection", () => {
    expect(initialFocusIndex(cells, null, 3)).toBe(5);
  });

  it("falls back to first day cell", () => {
    expect(initialFocusIndex(cells, null, null)).toBe(3);
  });

  it("moves focus by day skipping null padding cells", () => {
    expect(moveFocusIndex(cells, 7, 1)).toBe(8);
    expect(moveFocusIndex(cells, 3, -1)).toBe(3);
  });

  it("moves focus by week (7 cells)", () => {
    expect(moveFocusIndex(cells, 3, 7)).toBe(10);
  });
});
