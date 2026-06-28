/** Filter strikes by numeric substring (raw or en-IN formatted without commas). */
export function filterStrikes(strikes: number[], query: string): number[] {
  const digits = query.replace(/\D/g, "");
  if (!digits) return strikes;
  return strikes.filter((k) => {
    if (String(k).includes(digits)) return true;
    return k.toLocaleString("en-IN").replace(/,/g, "").includes(digits);
  });
}
