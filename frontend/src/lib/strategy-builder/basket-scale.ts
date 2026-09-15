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

/** Real margin for `units` copies of the basket's irreducible unit (see `activeLotsGcd`). */
export type MarginProbe = (units: number) => Promise<number>;

export type MarginScaleResult =
  | {
      ok: true;
      k: number;
      /** Measured margin at `k` units — never above the target. */
      margin: number;
      /** Margin quoted at the straight-line size when that came back over target, else null. */
      linearOvershoot: number | null;
    }
  | {
      ok: false;
      reason: "invalid-base" | "invalid-target" | "underflow";
      /** Smallest size measured over target (`underflow` only). */
      smallestOver?: { units: number; margin: number };
    };

type MarginPoint = { units: number; margin: number };

/** Next size to probe: the secant between the bracketing quotes, else a chord through the origin. */
function nextMarginProbe(
  fit: MarginPoint | null,
  over: MarginPoint | null,
  target: number,
): number {
  if (fit && over) {
    const slope = (over.margin - fit.margin) / (over.units - fit.units);
    if (slope > 0) return Math.floor(fit.units + (target - fit.margin) / slope);
  }
  const ref = (over ?? fit)!;
  return Math.floor((ref.units * target) / ref.margin);
}

/**
 * Largest whole number of units whose MEASURED margin fits `target`.
 *
 * Margin is not linear in lot count — ICICI's hedge credit can fall away as a
 * basket grows, so a straight-line size from one quote can overshoot badly (a
 * 1:1 call spread quoted at most ~₹22K/lot small came back ~₹58K/lot at 126
 * lots). The straight-line size is probed first; after that each probe sits on
 * the secant between the largest size known to fit and the smallest known not
 * to, which under-sizes whenever margin grows faster than lots. Only a measured
 * fit is ever returned, and each probe is an ICICI call against the per-minute
 * budget, so at most `maxProbes` are made. Same two-point idea as the portfolio
 * sizer (docs/strategy-builder-portfolio-margin-plan.md, D5).
 */
export async function solveMarginScale(params: {
  currentUnits: number;
  currentMargin: number;
  target: number;
  measure: MarginProbe;
  maxProbes?: number;
}): Promise<MarginScaleResult> {
  const { currentUnits, currentMargin, target, measure, maxProbes = 3 } = params;
  if (!(currentUnits >= 1 && Number.isFinite(currentMargin) && currentMargin > 0)) {
    return { ok: false, reason: "invalid-base" };
  }
  if (!(Number.isFinite(target) && target > 0)) {
    return { ok: false, reason: "invalid-target" };
  }
  const start = { units: currentUnits, margin: currentMargin };
  let fit: MarginPoint | null = currentMargin <= target ? start : null;
  let over: MarginPoint | null = fit ? null : start;
  let linearOvershoot: number | null = null;
  let next = nextMarginProbe(fit, over, target);
  for (let probe = 0; probe < maxProbes; probe++) {
    if (over) next = Math.min(next, over.units - 1);
    if (next < (fit ? fit.units + 1 : 1)) break;
    const margin = await measure(next);
    if (!Number.isFinite(margin)) {
      throw new Error("ICICI did not return a margin figure");
    }
    if (margin <= target) {
      fit = { units: next, margin };
    } else {
      if (probe === 0) linearOvershoot = margin;
      over = { units: next, margin };
    }
    next = nextMarginProbe(fit, over, target);
  }
  if (fit) return { ok: true, k: fit.units, margin: fit.margin, linearOvershoot };
  return { ok: false, reason: "underflow", smallestOver: over ?? undefined };
}
