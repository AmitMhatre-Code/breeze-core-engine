/** Compact ₹ display using Lac / Crore (and K below 1 Lac). */

const LAC = 100_000;
const CRORE = 10_000_000;

export function formatIndianMoneyCompact(
  amount: number,
  opts?: { shortSuffix?: boolean; skipK?: boolean; lacDecimals?: number },
): string {
  if (!Number.isFinite(amount)) return "—";
  const abs = Math.abs(amount);
  const wrapNegative = (s: string) => (amount < 0 ? `(${s})` : s);
  const short = opts?.shortSuffix ?? false;
  const sep = short ? "" : " ";
  if (abs >= CRORE) {
    return wrapNegative(
      `₹${(abs / CRORE).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}${sep}Cr`,
    );
  }
  if (abs >= LAC) {
    const lacDp = opts?.lacDecimals ?? 2;
    return wrapNegative(
      `₹${(abs / LAC).toLocaleString("en-IN", { minimumFractionDigits: lacDp, maximumFractionDigits: lacDp })}${sep}${short ? "L" : "Lac"}`,
    );
  }
  if (!opts?.skipK && abs >= 1000) {
    return wrapNegative(
      `₹${(abs / 1000).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}${sep}K`,
    );
  }
  return wrapNegative(
    `₹${abs.toLocaleString("en-IN", { maximumFractionDigits: 0 })}`,
  );
}

/**
 * Margin/capital figures (free margin, margin used, SPAN, ELM, capital) shown
 * on the navbar, Dashboard and Portfolio. Crore ≥ ₹1 Cr (2 dp), Lakh in the
 * ₹1 L–₹1 Cr band (1 dp, "L" suffix), and the shared K / plain-₹ fallback below
 * ₹1 L. Deliberately separate from formatIndianMoneyCompact's default so P&L and
 * per-leg values keep their existing 2-dp Lakh format.
 */
export function formatMarginCompact(amount: number): string {
  return formatIndianMoneyCompact(amount, { shortSuffix: true, lacDecimals: 1 });
}

export function moneyToneClass(amount: number): string {
  if (!Number.isFinite(amount)) return "text-zinc-900 dark:text-zinc-100";
  if (amount < 0) return "text-red-600 dark:text-red-400";
  if (amount > 0) return "text-emerald-600 dark:text-emerald-400";
  return "text-zinc-900 dark:text-zinc-100";
}
