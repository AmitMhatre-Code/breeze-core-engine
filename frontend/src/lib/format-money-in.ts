/** Compact ₹ display using Lac / Crore (and K below 1 Lac). */

const LAC = 100_000;
const CRORE = 10_000_000;

export function formatIndianMoneyCompact(amount: number): string {
  if (!Number.isFinite(amount)) return "—";
  const abs = Math.abs(amount);
  const wrapNegative = (s: string) => (amount < 0 ? `(${s})` : s);
  if (abs >= CRORE) {
    return wrapNegative(
      `₹${(abs / CRORE).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} Cr`,
    );
  }
  if (abs >= LAC) {
    return wrapNegative(
      `₹${(abs / LAC).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} Lac`,
    );
  }
  if (abs >= 1000) {
    return wrapNegative(
      `₹${(abs / 1000).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} K`,
    );
  }
  return wrapNegative(
    `₹${abs.toLocaleString("en-IN", { maximumFractionDigits: 0 })}`,
  );
}

export function moneyToneClass(amount: number): string {
  if (!Number.isFinite(amount)) return "text-zinc-900 dark:text-zinc-100";
  if (amount < 0) return "text-red-600 dark:text-red-400";
  if (amount > 0) return "text-emerald-600 dark:text-emerald-400";
  return "text-zinc-900 dark:text-zinc-100";
}
