import { formatStrike, normalizeStrikeKey } from "@/lib/strategy-builder/format-strike";

/** Filter strikes by numeric prefix (supports fractional strikes). */
export function filterStrikes(strikes: number[], query: string): number[] {
  const normalized = query.replace(/,/g, "").trim();
  if (!normalized) return strikes;
  const prefix = normalized.replace(/[^\d.]/g, "");
  if (!prefix) return strikes;
  return strikes.filter((k) => {
    const raw = normalizeStrikeKey(k);
    const formatted = formatStrike(k).replace(/,/g, "");
    return raw.startsWith(prefix) || formatted.startsWith(prefix);
  });
}
