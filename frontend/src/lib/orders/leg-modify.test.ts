import { describe, expect, it } from "vitest";
import { legOpenQuantity, parseNonNegativeInt } from "./leg-modify";

describe("legOpenQuantity", () => {
  it("sums pending_quantity only for modifiable orders", () => {
    const orders = [
      { order_id: "1", pending_quantity: 25, modifiable: true }, // open
      { order_id: "2", pending_quantity: 0, modifiable: false }, // executed
      { order_id: "3", pending_quantity: 25, modifiable: false }, // cancelled, never filled
      { order_id: "4", pending_quantity: 10, modifiable: true }, // partially executed, still open
    ];
    expect(legOpenQuantity(orders)).toBe(35);
  });

  it("returns 0 when no orders are modifiable", () => {
    const orders = [
      { order_id: "1", pending_quantity: 50, modifiable: false },
      { order_id: "2", pending_quantity: 25, modifiable: false },
    ];
    expect(legOpenQuantity(orders)).toBe(0);
  });

  it("returns 0 for an empty leg", () => {
    expect(legOpenQuantity([])).toBe(0);
  });

  it("treats missing pending_quantity as 0", () => {
    const orders = [{ order_id: "1", modifiable: true }];
    expect(legOpenQuantity(orders)).toBe(0);
  });
});

describe("parseNonNegativeInt", () => {
  it("accepts 0", () => {
    expect(parseNonNegativeInt("0")).toBe(0);
  });

  it("accepts positive integers", () => {
    expect(parseNonNegativeInt("50")).toBe(50);
  });

  it("rejects negative numbers", () => {
    expect(parseNonNegativeInt("-1")).toBeNull();
  });

  it("rejects non-numeric input", () => {
    expect(parseNonNegativeInt("abc")).toBeNull();
    expect(parseNonNegativeInt("")).toBeNull();
  });
});
