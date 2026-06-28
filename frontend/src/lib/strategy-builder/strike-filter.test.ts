import { describe, expect, it } from "vitest";
import { filterStrikes } from "@/lib/strategy-builder/strike-filter";

describe("filterStrikes", () => {
  const strikes = [24000, 24500, 25000, 25500];

  it("returns all strikes when query is empty", () => {
    expect(filterStrikes(strikes, "")).toEqual(strikes);
    expect(filterStrikes(strikes, "abc")).toEqual(strikes);
  });

  it("matches numeric prefix on raw strike", () => {
    expect(filterStrikes(strikes, "245")).toEqual([24500]);
    expect(filterStrikes(strikes, "25")).toEqual([25000, 25500]);
  });

  it("matches digits stripped from formatted labels", () => {
    expect(filterStrikes(strikes, "24,500")).toEqual([24500]);
  });
});
