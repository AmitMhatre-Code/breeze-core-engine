import type { StrategyLeg } from "@/lib/strategy-builder/types";
import {
  bsCallPrice,
  bsPutPrice,
  bsCallDelta,
  bsPutDelta,
  bsGamma,
  bsVega,
  bsCallTheta,
  bsPutTheta,
  DEFAULT_Q,
  DEFAULT_R,
} from "@/lib/strategy-builder/blackScholes";

function intrinsic(S: number, K: number, right: "Call" | "Put"): number {
  return right === "Call" ? Math.max(0, S - K) : Math.max(0, K - S);
}

/** A flat number applies to every leg; a function resolves each leg's own sigma (e.g. from a
 * per-strike IV smile) — used by the deterministic (non-Monte-Carlo) pricing/greeks functions
 * below, which price each leg independently and so have no shared-path constraint. */
export type SigmaInput = number | ((leg: StrategyLeg) => number);

function resolveSigma(sigma: SigmaInput, leg: StrategyLeg): number {
  return typeof sigma === "function" ? sigma(leg) : sigma;
}

/** Strike from a leg (chain / UI may surface strings — must not be dropped from exact payoff breakpoints). */
function finiteStrikeFromLeg(leg: StrategyLeg): number | null {
  const raw = leg.strike as unknown;
  if (typeof raw === "number" && Number.isFinite(raw)) return raw;
  if (typeof raw === "string") {
    const n = parseFloat(raw.replace(/,/g, ""));
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

function finiteLots(leg: StrategyLeg): number {
  const raw = leg.lots as unknown;
  if (typeof raw === "number" && Number.isFinite(raw)) return Math.max(0, raw);
  if (typeof raw === "string") {
    const n = parseFloat(raw.replace(/,/g, ""));
    return Number.isFinite(n) ? Math.max(0, n) : 0;
  }
  return 0;
}

/** P&amp;L at expiry in rupee terms (approx): lots × lotSize × (direction × intrinsic − signed premium flow). */
export function portfolioPayoffAtExpiry(
  spot: number,
  legs: StrategyLeg[],
  lotSize: number,
): number {
  const ls =
    typeof lotSize === "number" && Number.isFinite(lotSize) && lotSize > 0
      ? lotSize
      : 0;
  let total = 0;
  for (const leg of legs) {
    const K = finiteStrikeFromLeg(leg);
    if (K == null) continue;
    const units = finiteLots(leg) * ls;
    const intr = intrinsic(spot, K, leg.right);
    const prem = leg.premiumPerUnit ?? 0;
    const premN = typeof prem === "number" && Number.isFinite(prem) ? prem : 0;
    if (leg.side === "Buy") {
      total += units * (intr - premN);
    } else {
      total += units * (premN - intr);
    }
  }
  return total;
}

/**
 * Spot-centered payoff chart domain: prices from spot×(1−f) to spot×(1+f), f = 0.4 → ±40%.
 * PayoffChart uses defaultSpanFraction 0.5 so the initial view is ±20% around spot; zoom-out reaches this full span.
 */
export const PAYOFF_CHART_SPOT_HALFBAND = 0.4;

export function payoffChartSpotDomain(spot: number): { minS: number; maxS: number } {
  const f = PAYOFF_CHART_SPOT_HALFBAND;
  return { minS: Math.max(0, spot * (1 - f)), maxS: spot * (1 + f) };
}

export function scanPayoffCurve(
  minS: number,
  maxS: number,
  steps: number,
  legs: StrategyLeg[],
  lotSize: number,
): { xs: number[]; ys: number[] } {
  const xs: number[] = [];
  const ys: number[] = [];
  if (steps < 2 || maxS <= minS) return { xs, ys };
  const step = (maxS - minS) / (steps - 1);
  for (let i = 0; i < steps; i++) {
    const S = minS + step * i;
    xs.push(S);
    ys.push(portfolioPayoffAtExpiry(S, legs, lotSize));
  }
  return { xs, ys };
}

export type PayoffSummary = {
  maxProfit: number;
  maxLoss: number;
  breakevens: number[];
};

export function summarizePayoffScan(xs: number[], ys: number[]): PayoffSummary {
  let maxProfit = -Infinity;
  let maxLoss = Infinity;
  for (const y of ys) {
    if (y > maxProfit) maxProfit = y;
    if (y < maxLoss) maxLoss = y;
  }
  if (!xs.length) {
    return { maxProfit: 0, maxLoss: 0, breakevens: [] };
  }
  const ymin = Math.min(...ys);
  const ymax = Math.max(...ys);
  /** Flat payoff (e.g. no legs, all zeros): every sample hits zero, which would falsely emit one breakeven per grid step. */
  if (Math.abs(ymax - ymin) <= 1e-12) {
    return {
      maxProfit: Number.isFinite(ymax) ? ymax : 0,
      maxLoss: Number.isFinite(ymin) ? ymin : 0,
      breakevens: [],
    };
  }
  const breakevens: number[] = [];
  for (let i = 1; i < xs.length; i++) {
    const y0 = ys[i - 1];
    const y1 = ys[i];
    if (y0 === 0) breakevens.push(xs[i - 1]);
    if (y0 * y1 < 0) {
      const t = y0 / (y0 - y1);
      breakevens.push(xs[i - 1] + t * (xs[i] - xs[i - 1]));
    }
  }
  return {
    maxProfit: Number.isFinite(maxProfit) ? maxProfit : 0,
    maxLoss: Number.isFinite(maxLoss) ? maxLoss : 0,
    breakevens,
  };
}

function uniqueSorted(values: number[]): number[] {
  const sorted = [...values].filter(Number.isFinite).sort((a, b) => a - b);
  const out: number[] = [];
  for (const v of sorted) {
    const prev = out[out.length - 1];
    if (prev == null || Math.abs(v - prev) > 1e-9) out.push(v);
  }
  return out;
}

function normalizeBreakevens(values: number[]): number[] {
  if (!values.length) return [];
  const sorted = [...values].filter(Number.isFinite).sort((a, b) => a - b);
  const out: number[] = [];
  for (const v of sorted) {
    const prev = out[out.length - 1];
    if (prev == null || Math.abs(v - prev) > 0.5) out.push(v);
  }
  return out;
}

/** Net call units (lots × lotSize, signed) governing payoff slope as spot → ∞. */
function netCallExposureUnits(legs: StrategyLeg[], lotSize: number): number {
  const ls =
    typeof lotSize === "number" && Number.isFinite(lotSize) && lotSize > 0
      ? lotSize
      : 0;
  let net = 0;
  for (const leg of legs) {
    if (leg.right !== "Call") continue;
    const units = finiteLots(leg) * ls;
    net += leg.side === "Buy" ? units : -units;
  }
  return net;
}

/**
 * Exact expiry summary from piecewise-linear option payoff.
 * Uses strike breakpoints (plus wide anchors) so breakevens/max P&L are not grid-approximate.
 */
export function summarizePayoffExact(
  legs: StrategyLeg[],
  lotSize: number,
  spot: number | null = null,
): PayoffSummary {
  if (!legs.length) return { maxProfit: 0, maxLoss: 0, breakevens: [] };
  const strikes = uniqueSorted(
    legs.flatMap((l) => {
      const k = finiteStrikeFromLeg(l);
      return k == null ? [] : [k];
    }),
  );
  const maxStrike = strikes.length ? strikes[strikes.length - 1] : 0;
  const ref = Math.max(maxStrike, spot ?? 0, 1);
  const left = 0;
  const right = ref * 4;
  const nodes = uniqueSorted([left, ...strikes, right]);
  const ys = nodes.map((s) => portfolioPayoffAtExpiry(s, legs, lotSize));
  let maxProfit = -Infinity;
  let maxLoss = Infinity;
  for (const y of ys) {
    if (y > maxProfit) maxProfit = y;
    if (y < maxLoss) maxLoss = y;
  }
  const breakevens: number[] = [];
  for (let i = 1; i < nodes.length; i++) {
    const x0 = nodes[i - 1];
    const x1 = nodes[i];
    const y0 = ys[i - 1];
    const y1 = ys[i];
    if (Math.abs(y0) <= 1e-9) breakevens.push(x0);
    if (y0 * y1 < 0) {
      const t = y0 / (y0 - y1);
      breakevens.push(x0 + t * (x1 - x0));
    }
  }
  if (Math.abs(ys[ys.length - 1]) <= 1e-9) breakevens.push(nodes[nodes.length - 1]);

  const netCalls = netCallExposureUnits(legs, lotSize);
  if (netCalls > 0) maxProfit = Infinity;
  if (netCalls < 0) maxLoss = -Infinity;

  return {
    maxProfit,
    maxLoss,
    breakevens: normalizeBreakevens(breakevens),
  };
}

export function portfolioMarkToModel(
  S: number,
  legs: StrategyLeg[],
  lotSize: number,
  T: number,
  sigma: SigmaInput,
  r: number = DEFAULT_R,
  q: number = DEFAULT_Q,
): number {
  let total = 0;
  for (const leg of legs) {
    const units = Math.max(0, leg.lots) * lotSize;
    const prem = leg.premiumPerUnit ?? 0;
    const legSigma = resolveSigma(sigma, leg);
    const model =
      leg.right === "Call"
        ? bsCallPrice(S, leg.strike, T, legSigma, r, q)
        : bsPutPrice(S, leg.strike, T, legSigma, r, q);
    if (leg.side === "Buy") {
      total += units * (model - prem);
    } else {
      total += units * (prem - model);
    }
  }
  return total;
}

export function scanMarkToModelCurve(
  minS: number,
  maxS: number,
  steps: number,
  legs: StrategyLeg[],
  lotSize: number,
  T: number,
  sigma: SigmaInput,
  r: number = DEFAULT_R,
  q: number = DEFAULT_Q,
): { xs: number[]; ys: number[] } {
  const xs: number[] = [];
  const ys: number[] = [];
  if (steps < 2 || maxS <= minS) return { xs, ys };
  const step = (maxS - minS) / (steps - 1);
  for (let i = 0; i < steps; i++) {
    const S = minS + step * i;
    xs.push(S);
    ys.push(portfolioMarkToModel(S, legs, lotSize, T, sigma, r, q));
  }
  return { xs, ys };
}

export type PortfolioGreeks = {
  delta: number;
  gamma: number;
  vega: number;
  thetaPerDay: number;
};

export function portfolioGreeks(
  S: number,
  legs: StrategyLeg[],
  lotSize: number,
  T: number,
  sigma: SigmaInput,
  r: number = DEFAULT_R,
  q: number = DEFAULT_Q,
): PortfolioGreeks {
  let delta = 0;
  let gamma = 0;
  let vega = 0;
  let theta = 0;
  for (const leg of legs) {
    const units = Math.max(0, leg.lots) * lotSize;
    const sign = leg.side === "Buy" ? 1 : -1;
    const legSigma = resolveSigma(sigma, leg);
    if (leg.right === "Call") {
      delta += sign * units * bsCallDelta(S, leg.strike, T, legSigma, r, q);
      gamma += sign * units * bsGamma(S, leg.strike, T, legSigma, r, q);
      vega += sign * units * bsVega(S, leg.strike, T, legSigma, r, q);
      theta += sign * units * bsCallTheta(S, leg.strike, T, legSigma, r, q);
    } else {
      delta += sign * units * bsPutDelta(S, leg.strike, T, legSigma, r, q);
      gamma += sign * units * bsGamma(S, leg.strike, T, legSigma, r, q);
      vega += sign * units * bsVega(S, leg.strike, T, legSigma, r, q);
      theta += sign * units * bsPutTheta(S, leg.strike, T, legSigma, r, q);
    }
  }
  return {
    delta,
    gamma,
    vega,
    thetaPerDay: theta / 365,
  };
}

/** Model-based POP: Monte Carlo terminal spot ~ lognormal; share of paths with positive expiry P&amp;L. */
export function estimateProbabilityOfProfit(
  spot: number,
  T: number,
  sigma: number,
  legs: StrategyLeg[],
  lotSize: number,
  r: number = DEFAULT_R,
  q: number = DEFAULT_Q,
  samples: number = 8000,
): number {
  if (T <= 0 || sigma <= 0 || spot <= 0 || samples < 1) return 0;
  const drift = (r - q - 0.5 * sigma * sigma) * T;
  const vol = sigma * Math.sqrt(T);
  let wins = 0;
  for (let i = 0; i < samples; i++) {
    const z = randomNormal();
    const ST = spot * Math.exp(drift + vol * z);
    if (portfolioPayoffAtExpiry(ST, legs, lotSize) > 0) wins++;
  }
  return (100 * wins) / samples;
}

function randomNormal(): number {
  const u = 1 - Math.random();
  const v = 1 - Math.random();
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
}
