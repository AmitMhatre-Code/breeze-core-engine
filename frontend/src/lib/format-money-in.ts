/** Compact ₹ display using Lac / Crore (and K below 1 Lac). */

const LAC = 100_000;
const CRORE = 10_000_000;

export function formatIndianMoneyCompact(amount: number): string {
  if (!Number.isFinite(amount)) return "—";
  const abs = Math.abs(amount);
  const sign = amount < 0 ? "-" : "";
  if (abs >= CRORE) {
    return `${sign}₹${(abs / CRORE).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} Cr`;
  }
  if (abs >= LAC) {
    return `${sign}₹${(abs / LAC).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} Lac`;
  }
  if (abs >= 1000) {
    return `${sign}₹${(abs / 1000).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} K`;
  }
  return `${sign}₹${abs.toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
}
