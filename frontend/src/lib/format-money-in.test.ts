import { describe, expect, it } from "vitest";
import {
  formatIndianMoneyCompact,
  formatMarginCompact,
} from "@/lib/format-money-in";

describe("formatMarginCompact", () => {
  it("keeps Crore at 2 decimals with the short suffix", () => {
    // ₹1.7570661 Cr (from the get_margin actual_margin_avl)
    expect(formatMarginCompact(17_570_661)).toBe("₹1.76Cr");
    expect(formatMarginCompact(52_921_856)).toBe("₹5.29Cr");
  });

  it("uses Lakhs with 1 decimal and an 'L' suffix below ₹1 Cr", () => {
    // free-after-ELM = ₹35.7 L
    expect(formatMarginCompact(3_570_661)).toBe("₹35.7L");
    expect(formatMarginCompact(100_000)).toBe("₹1.0L");
  });

  it("falls back to K / plain-₹ below ₹1 Lakh", () => {
    expect(formatMarginCompact(45_231)).toBe("₹45.23K");
    expect(formatMarginCompact(0)).toBe("₹0");
  });

  it("does not disturb the default Lakh formatting (still 2dp) for P&L etc.", () => {
    expect(formatIndianMoneyCompact(3_570_661, { shortSuffix: true })).toBe(
      "₹35.71L",
    );
  });
});
