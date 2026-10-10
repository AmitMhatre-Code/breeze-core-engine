import { describe, expect, it } from "vitest";

import { lossConfirmed, lossToType } from "@/lib/backtest-loss";

describe("typing a losing backtest's loss before going unattended", () => {
  it("asks only when the backtest lost money", () => {
    expect(lossToType(1200)).toBeNull();
    expect(lossToType(0)).toBeNull();
    expect(lossToType(null)).toBeNull();
    expect(lossToType(-43300.6)).toBe("43301");
    expect(lossConfirmed(1200, "")).toBe(true);
    expect(lossConfirmed(undefined, "")).toBe(true);
  });

  it("matches the digits however they are written", () => {
    expect(lossConfirmed(-43301, "43301")).toBe(true);
    expect(lossConfirmed(-43301, "₹43,301")).toBe(true);
    expect(lossConfirmed(-43301, "-43301")).toBe(true);
    expect(lossConfirmed(-43301, "4330")).toBe(false);
    expect(lossConfirmed(-43301, "")).toBe(false);
  });
});
