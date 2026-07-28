import { describe, expect, it } from "vitest";
import { getAvailableMargin, getHomeMarginTiles } from "@/lib/home-data";

// Shape mirrors get_margin_situation Success (see build_margin_situation_from_raw):
// actual_margin_avl = cash_limit + actual_margin_ute, where ute is negative when
// margin is utilised. From the 27-Jul-2026 get_margin payload.
const margin = {
  Status: 200,
  Success: {
    cash_limit: 52_921_856.42,
    actual_margin_ute: -35_351_195.29,
    actual_margin_avl: 17_570_661.13,
  },
};

describe("getHomeMarginTiles", () => {
  it("reports margin used as cash − available (capital actually blocked)", () => {
    const { funds, marginUsed } = getHomeMarginTiles(margin);
    // Free margin = ICICI's available margin.
    expect(funds).toBeCloseTo(17_570_661.13, 2);
    // Regression: previously computed avl − cash and clamped to 0, so a fully
    // utilised account showed "Margin used = ₹0". Must now be cash − avl.
    expect(marginUsed).toBeCloseTo(35_351_195.29, 2);
  });

  it("used + free tallies to total capital (cash_limit)", () => {
    const { funds, marginUsed } = getHomeMarginTiles(margin);
    expect((funds ?? 0) + (marginUsed ?? 0)).toBeCloseTo(52_921_856.42, 2);
  });

  it("clamps margin used to 0 when available exceeds cash (extra allocation)", () => {
    const { marginUsed } = getHomeMarginTiles({
      Status: 200,
      Success: { cash_limit: 1_000_000, actual_margin_avl: 1_200_000 },
    });
    expect(marginUsed).toBe(0);
  });

  it("getAvailableMargin returns the same available figure used by the navbar", () => {
    expect(getAvailableMargin(margin)).toBeCloseTo(17_570_661.13, 2);
  });
});
