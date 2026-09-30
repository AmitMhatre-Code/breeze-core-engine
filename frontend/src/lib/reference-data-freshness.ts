/** The warning for reference data older than the latest concluded session. */
export function staleSourcesText(
  stale: string[],
  scripLoadedAt: string | null,
  loading: boolean,
): string {
  const parts: string[] = [];
  if (stale.includes("scrip")) {
    parts.push(
      `The ICICI scrip master was last loaded ${scripLoadedAt ? scripLoadedAt.replace("T", " ").slice(0, 16) : "never"}, before the last session closed. Contracts that became tradeable since then have no live quote.`,
    );
  }
  const bhav = stale.filter((s) => s !== "scrip");
  if (bhav.length) parts.push(`The ${bhav.join(" and ")} bhavcopy is older than the last session.`);
  parts.push(loading ? "A load is running now." : "Use Load now, or wait for the automatic retry.");
  return parts.join(" ");
}
