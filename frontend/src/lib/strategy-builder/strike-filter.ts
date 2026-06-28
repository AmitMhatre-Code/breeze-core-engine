/** Filter strikes by numeric prefix (raw or en-IN formatted without commas). */
export function filterStrikes(strikes: number[], query: string): number[] {
  const digits = query.replace(/\D/g, "");
  if (!digits) return strikes;
  return strikes.filter((k) => {
    const raw = String(k);
    const formatted = k.toLocaleString("en-IN").replace(/,/g, "");
    return raw.startsWith(digits) || formatted.startsWith(digits);
  });
}
