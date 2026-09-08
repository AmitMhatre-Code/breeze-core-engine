/** Which source a Dashboard P&L tile is showing, and whether it is a live value.
 *
 * The backend withholds a live figure whenever it could not price every leg — after
 * the close, and during any feed gap — rather than publishing a total built from
 * legs it had no price for. That total was previously ₹0 for an entirely unpriced
 * book, which is a number the tile could not tell apart from a genuinely flat one,
 * and which outranked the complete REST snapshot already loaded in the page.
 */

import type { DayPnlResponse } from "@/lib/dashboard-day-pnl";

export type TileSource<T> = {
  value: T | null;
  /** True when the value came from the REST snapshot rather than the live feed. */
  isSnapshot: boolean;
};

export function resolveOpenPnl(
  live: number | null | undefined,
  snapshot: number | null | undefined,
): TileSource<number> {
  if (typeof live === "number") return { value: live, isSnapshot: false };
  return {
    value: typeof snapshot === "number" ? snapshot : null,
    isSnapshot: typeof snapshot === "number",
  };
}

export function resolveDayPnl(
  live: DayPnlResponse | null | undefined,
  snapshot: DayPnlResponse | null | undefined,
): TileSource<DayPnlResponse> {
  // A live payload that withheld its total is no more useful than none at all; the
  // snapshot is the better answer in both cases.
  if (live && live.total_day_pnl != null) return { value: live, isSnapshot: false };
  return { value: snapshot ?? null, isSnapshot: Boolean(snapshot) };
}
