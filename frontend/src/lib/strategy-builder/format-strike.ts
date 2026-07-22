/** Canonical strike string for API/SPAN keys (matches backend strike_key). */
export function normalizeStrikeKey(strike: number): string {
  if (!Number.isFinite(strike) || strike <= 0) return "";
  const text = String(strike);
  if (!text.includes(".")) return text;
  return text
    .replace(/(\.\d*?[1-9])0+$/u, "$1")
    .replace(/\.0+$/u, "");
}

/** Display strike with en-IN grouping; show decimals only when needed. */
export function formatStrike(strike: number): string {
  if (!Number.isFinite(strike)) return "—";
  const key = normalizeStrikeKey(strike);
  const num = Number(key);
  if (!Number.isFinite(num)) return "—";
  const hasFraction = key.includes(".");
  return num.toLocaleString("en-IN", {
    minimumFractionDigits: hasFraction ? 2 : 0,
    maximumFractionDigits: hasFraction ? 2 : 0,
  });
}
