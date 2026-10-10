/** The typed confirmation in front of unattended trading on a losing backtest (#76).
 *
 *  Never a block: the user chose to be warned, not refused. Typing the loss is asked for only
 *  when the backtest of the exact settings lost money, so it cannot become a reflex the way a
 *  phrase typed on every confirmation would. */

/** The whole-rupee loss to type, or null when there is nothing to confirm. */
export function lossToType(netPnl: number | null | undefined): string | null {
  if (netPnl === null || netPnl === undefined || !(netPnl < 0)) return null;
  return String(Math.round(Math.abs(netPnl)));
}

/** True when nothing needs confirming, or the digits typed are the loss. Commas, ₹, a minus sign
 *  and spaces are ignored, so "₹43,301" and "-43301" both match. */
export function lossConfirmed(netPnl: number | null | undefined, typed: string): boolean {
  const expected = lossToType(netPnl);
  if (expected === null) return true;
  return typed.replace(/[^\d]/g, "") === expected;
}
