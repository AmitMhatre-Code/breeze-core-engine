import { expiryDisplayToYears } from "@/lib/strategy-builder/expiry";
import { proposedLegsToStrategyLegs } from "@/lib/strategy-builder/map-proposed-legs";
import { estimateProbabilityOfProfit } from "@/lib/strategy-builder/payoff";
import type { ProposedTrade } from "@/lib/strategy-builder/types";

export function isUnlimitedMaxLoss(maxLoss: number | null | undefined): boolean {
  return maxLoss == null || !Number.isFinite(maxLoss);
}

/** Parse backend `"loss : profit"` into profit/loss ratio to 2 decimal places. */
export function formatRiskRewardRatio(
  riskReward: string | null | undefined,
  maxLoss: number | null | undefined,
): string {
  if (isUnlimitedMaxLoss(maxLoss)) return "∞";
  if (!riskReward?.trim()) return "—";
  const parts = riskReward.split(":").map((s) => parseFloat(s.trim()));
  if (parts.length !== 2 || !parts.every((n) => Number.isFinite(n) && n > 0)) {
    return "—";
  }
  const [loss, profit] = parts;
  return (profit / loss).toFixed(2);
}

export function computeTradePop(
  trade: ProposedTrade,
  spot: number | null,
  atmIv: number | null,
  expiryDate: string,
  lotSize: number,
): number | null {
  if (trade.status === "skipped" || spot == null || !trade.legs.length) return null;
  const T = expiryDisplayToYears(expiryDate);
  const sigma = atmIv != null && atmIv > 0 ? atmIv : 0.2;
  const legs = proposedLegsToStrategyLegs(trade.legs, lotSize);
  return estimateProbabilityOfProfit(spot, T, sigma, legs, lotSize);
}
