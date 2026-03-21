/** Mirrors backend iv_compute defaults for consistency. */
export const DEFAULT_R = 0.07;
export const DEFAULT_Q = 0;

const SQRT_2PI = Math.sqrt(2 * Math.PI);

export function normPdf(x: number): number {
  return Math.exp(-0.5 * x * x) / SQRT_2PI;
}

export function normCdf(x: number): number {
  return 0.5 * (1 + erf(x / Math.SQRT2));
}

function erf(x: number): number {
  const sign = x < 0 ? -1 : 1;
  const ax = Math.abs(x);
  const a1 = 0.254829592;
  const a2 = -0.284496736;
  const a3 = 1.421413741;
  const a4 = -1.453152027;
  const a5 = 1.061405429;
  const p = 0.3275911;
  const t = 1 / (1 + p * ax);
  const y =
    1 - ((((a5 * t + a4) * t + a3) * t + a2) * t + a1) * t * Math.exp(-ax * ax);
  return sign * y;
}

function d12(
  S: number,
  K: number,
  T: number,
  r: number,
  q: number,
  sigma: number,
): { d1: number; d2: number } {
  if (T <= 0 || sigma <= 0) {
    return { d1: 0, d2: 0 };
  }
  const sqrtT = Math.sqrt(T);
  const d1 =
    (Math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / (sigma * sqrtT);
  const d2 = d1 - sigma * sqrtT;
  return { d1, d2 };
}

export function bsCallPrice(
  S: number,
  K: number,
  T: number,
  sigma: number,
  r: number = DEFAULT_R,
  q: number = DEFAULT_Q,
): number {
  if (T <= 0) return Math.max(0, S - K);
  if (sigma <= 0) return Math.max(0, S - K);
  const { d1, d2 } = d12(S, K, T, r, q, sigma);
  return (
    S * Math.exp(-q * T) * normCdf(d1) - K * Math.exp(-r * T) * normCdf(d2)
  );
}

export function bsPutPrice(
  S: number,
  K: number,
  T: number,
  sigma: number,
  r: number = DEFAULT_R,
  q: number = DEFAULT_Q,
): number {
  if (T <= 0) return Math.max(0, K - S);
  if (sigma <= 0) return Math.max(0, K - S);
  const { d1, d2 } = d12(S, K, T, r, q, sigma);
  return (
    K * Math.exp(-r * T) * normCdf(-d2) - S * Math.exp(-q * T) * normCdf(-d1)
  );
}

export function bsCallDelta(
  S: number,
  K: number,
  T: number,
  sigma: number,
  r: number = DEFAULT_R,
  q: number = DEFAULT_Q,
): number {
  if (T <= 0 || sigma <= 0) return S > K ? 1 : 0;
  const { d1 } = d12(S, K, T, r, q, sigma);
  return Math.exp(-q * T) * normCdf(d1);
}

export function bsPutDelta(
  S: number,
  K: number,
  T: number,
  sigma: number,
  r: number = DEFAULT_R,
  q: number = DEFAULT_Q,
): number {
  if (T <= 0 || sigma <= 0) return S < K ? -1 : 0;
  const { d1 } = d12(S, K, T, r, q, sigma);
  return Math.exp(-q * T) * (normCdf(d1) - 1);
}

export function bsGamma(
  S: number,
  K: number,
  T: number,
  sigma: number,
  r: number = DEFAULT_R,
  q: number = DEFAULT_Q,
): number {
  if (T <= 0 || sigma <= 0 || S <= 0) return 0;
  const { d1 } = d12(S, K, T, r, q, sigma);
  const sqrtT = Math.sqrt(T);
  return (
    (Math.exp(-q * T) * normPdf(d1)) / (S * sigma * sqrtT)
  );
}

export function bsVega(
  S: number,
  K: number,
  T: number,
  sigma: number,
  r: number = DEFAULT_R,
  q: number = DEFAULT_Q,
): number {
  if (T <= 0 || sigma <= 0) return 0;
  const { d1 } = d12(S, K, T, r, q, sigma);
  return S * Math.exp(-q * T) * normPdf(d1) * Math.sqrt(T);
}

/** Theta per year (divide by 365 for per day). */
export function bsCallTheta(
  S: number,
  K: number,
  T: number,
  sigma: number,
  r: number = DEFAULT_R,
  q: number = DEFAULT_Q,
): number {
  if (T <= 0 || sigma <= 0) return 0;
  const { d1, d2 } = d12(S, K, T, r, q, sigma);
  const sqrtT = Math.sqrt(T);
  const term1 =
    (-S * Math.exp(-q * T) * normPdf(d1) * sigma) / (2 * sqrtT);
  const term2 = q * S * Math.exp(-q * T) * normCdf(d1);
  const term3 = -r * K * Math.exp(-r * T) * normCdf(d2);
  return term1 - term2 + term3;
}

export function bsPutTheta(
  S: number,
  K: number,
  T: number,
  sigma: number,
  r: number = DEFAULT_R,
  q: number = DEFAULT_Q,
): number {
  if (T <= 0 || sigma <= 0) return 0;
  const { d1, d2 } = d12(S, K, T, r, q, sigma);
  const sqrtT = Math.sqrt(T);
  const term1 =
    (-S * Math.exp(-q * T) * normPdf(d1) * sigma) / (2 * sqrtT);
  const term2 = -q * S * Math.exp(-q * T) * normCdf(-d1);
  const term3 = r * K * Math.exp(-r * T) * normCdf(-d2);
  return term1 + term2 + term3;
}

export function impliedVolatility(
  optionType: "call" | "put",
  price: number,
  S: number,
  K: number,
  T: number,
  r: number = DEFAULT_R,
  q: number = DEFAULT_Q,
): number | null {
  if (price <= 0 || S <= 0 || K <= 0 || T <= 0) return null;
  const intrinsic =
    optionType === "call" ? Math.max(0, S - K) : Math.max(0, K - S);
  if (price < intrinsic - 1e-6) return null;

  let lo = 0.01;
  let hi = 3.0;
  const f = (sig: number) =>
    optionType === "call"
      ? bsCallPrice(S, K, T, sig, r, q) - price
      : bsPutPrice(S, K, T, sig, r, q) - price;

  if (f(lo) > 0) return lo;
  if (f(hi) < 0) return null;

  for (let i = 0; i < 80; i++) {
    const mid = 0.5 * (lo + hi);
    const v = f(mid);
    if (Math.abs(v) < 1e-6) return mid;
    if (v > 0) hi = mid;
    else lo = mid;
  }
  return 0.5 * (lo + hi);
}
