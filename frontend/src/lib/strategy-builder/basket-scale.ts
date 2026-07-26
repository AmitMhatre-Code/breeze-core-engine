import type { OrderSide } from "@/lib/strategy-builder/types";

/** Which resource the basket is scaled against. */
export type ScaleMode = "margin" | "premium";

export type ScaleLeg = {
  lots: number;
  side: OrderSide;
  /**
   * Resolved per-unit premium — a fixed price, or the last-known chain mid for
   * aggressive-limit legs. `undefined` when no price is known (unpriced leg).
   */
  unitPrice: number | undefined;
};

/**
 * Net cash outlay at the given lots: positive = net debit (cash leaves the
 * account on long legs), negative = net credit (premium collected on shorts).
 * Inactive (`lots <= 0`) and unpriced legs contribute nothing.
 */
export function computeNetDebit(legs: ScaleLeg[], lotSize: number): number {
  let debit = 0;
  for (const l of legs) {
    if (!(l.lots > 0)) continue;
    const price = l.unitPrice;
    if (price == null || !Number.isFinite(price) || price <= 0) continue;
    const amount = price * l.lots * lotSize;
    debit += l.side === "Buy" ? amount : -amount;
  }
  return debit;
}

/**
 * True when some active leg has no usable price — its premium can't enter the
 * net-debit base, so a premium target computed from it would under-count.
 */
export function hasUnpricedActiveLeg(legs: ScaleLeg[]): boolean {
  return legs.some(
    (l) =>
      l.lots > 0 &&
      !(l.unitPrice != null && Number.isFinite(l.unitPrice) && l.unitPrice > 0),
  );
}

/** Suggested default mode from basket composition: net-debit → premium, else margin. */
export function suggestScaleMode(netDebit: number): ScaleMode {
  return netDebit > 0 ? "premium" : "margin";
}

function gcd2(a: number, b: number): number {
  a = Math.abs(a);
  b = Math.abs(b);
  while (b) {
    [a, b] = [b, a % b];
  }
  return a;
}

/**
 * GCD of the active (`lots > 0`) legs' lot counts — the size, in lots, of the
 * strategy's irreducible unit basket. Dividing each active leg's lots by this
 * yields the smallest lot ratio that preserves the strategy exactly, which is
 * the granularity `Scale` snaps to (so a basket can be scaled down as well as
 * up). Returns 0 when no leg is active.
 */
export function activeLotsGcd(legs: { lots: number }[]): number {
  let g = 0;
  for (const l of legs) {
    if (!(l.lots > 0)) continue;
    g = gcd2(g, Math.round(l.lots));
  }
  return g;
}

export type ScaleMultiplier =
  | { ok: true; k: number }
  | { ok: false; reason: "invalid-base" | "invalid-target" | "underflow" };

/**
 * Largest integer `k` such that `k * base <= target`, so every leg's lots can be
 * multiplied by the same whole number (the strategy's leg ratio is preserved
 * exactly). `invalid-base` means the resource this mode scales against is absent
 * (no margin-bearing leg, or a net-credit basket for premium mode); `underflow`
 * means even a single base basket already exceeds the target.
 */
export function computeScaleMultiplier(
  base: number,
  target: number,
): ScaleMultiplier {
  if (!(Number.isFinite(base) && base > 0)) {
    return { ok: false, reason: "invalid-base" };
  }
  if (!(Number.isFinite(target) && target > 0)) {
    return { ok: false, reason: "invalid-target" };
  }
  const k = Math.floor(target / base);
  if (k < 1) return { ok: false, reason: "underflow" };
  return { ok: true, k };
}
