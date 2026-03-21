import { describe, expect, it } from "vitest";
import {
  bsCallDelta,
  bsCallPrice,
  bsPutPrice,
  impliedVolatility,
} from "@/lib/strategy-builder/blackScholes";

describe("blackScholes", () => {
  it("matches put–call parity at ATM", () => {
    const S = 100;
    const K = 100;
    const T = 0.25;
    const sigma = 0.2;
    const r = 0.07;
    const q = 0;
    const c = bsCallPrice(S, K, T, sigma, r, q);
    const p = bsPutPrice(S, K, T, sigma, r, q);
    const lhs = c - p;
    const rhs = S * Math.exp(-q * T) - K * Math.exp(-r * T);
    expect(Math.abs(lhs - rhs)).toBeLessThan(1e-6);
  });

  it("recovers sigma from call price", () => {
    const S = 24000;
    const K = 24000;
    const T = 20 / 365;
    const sigma = 0.18;
    const price = bsCallPrice(S, K, T, sigma);
    const iv = impliedVolatility("call", price, S, K, T);
    expect(iv).not.toBeNull();
    expect(Math.abs((iv ?? 0) - sigma)).toBeLessThan(1e-3);
  });

  it("ATM call delta near 0.5 for short-dated option", () => {
    const d = bsCallDelta(100, 100, 0.1, 0.25);
    expect(d).toBeGreaterThan(0.45);
    expect(d).toBeLessThan(0.58);
  });
});
