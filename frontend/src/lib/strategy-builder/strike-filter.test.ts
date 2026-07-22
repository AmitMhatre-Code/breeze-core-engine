import { describe, expect, it } from "vitest";
import { formatStrike, normalizeStrikeKey } from "@/lib/strategy-builder/format-strike";
import { filterStrikes } from "@/lib/strategy-builder/strike-filter";

describe("formatStrike", () => {
  it("formats whole numbers without decimals", () => {
    expect(formatStrike(24000)).toBe("24,000");
  });

  it("formats fractional strikes", () => {
    expect(formatStrike(150.35)).toBe("150.35");
  });

  it("normalizes strike keys", () => {
    expect(normalizeStrikeKey(150)).toBe("150");
    expect(normalizeStrikeKey(150.35)).toBe("150.35");
  });
});

describe("filterStrikes", () => {
  const strikes = [24000, 24500, 25000, 25050, 25500, 150.35];

  it("returns all strikes when query is empty", () => {
    expect(filterStrikes(strikes, "")).toEqual(strikes);
    expect(filterStrikes(strikes, "abc")).toEqual(strikes);
  });

  it("matches numeric prefix on raw strike", () => {
    expect(filterStrikes(strikes, "245")).toEqual([24500]);
    expect(filterStrikes(strikes, "25")).toEqual([25000, 25050, 25500]);
    expect(filterStrikes(strikes, "250")).toEqual([25000, 25050]);
  });

  it("does not match strikes where digits appear mid-string", () => {
    expect(filterStrikes(strikes, "25")).not.toContain(24500);
  });

  it("matches digits stripped from formatted labels", () => {
    expect(filterStrikes(strikes, "24,500")).toEqual([24500]);
  });

  it("matches fractional strike prefix", () => {
    expect(filterStrikes(strikes, "150.3")).toEqual([150.35]);
  });
});
