import { blendedSigmaForLegs, type SigmaSmiles } from "@/lib/strategy-builder/chainIv";
import { expiryDisplayToYears } from "@/lib/strategy-builder/expiry";
import { proposedLegsToStrategyLegs } from "@/lib/strategy-builder/map-proposed-legs";
import { estimateProbabilityOfProfit } from "@/lib/strategy-builder/payoff";
import type { ProposedTrade } from "@/lib/strategy-builder/types";

export function isUnlimitedMaxLoss(maxLoss: number | null | undefined): boolean {
  return maxLoss == null || !Number.isFinite(maxLoss);
}

export function isUnlimitedMaxProfit(
  maxProfit: number | null | undefined,
): boolean {
  return maxProfit != null && !Number.isFinite(maxProfit) && maxProfit > 0;
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
  sigmaSmiles: SigmaSmiles | null = null,
): number | null {
  if (trade.status === "skipped" || !trade.legs.length) return null;
  if (trade.pop_pct != null && Number.isFinite(trade.pop_pct)) return trade.pop_pct;
  if (spot == null) return null;
  const T = expiryDisplayToYears(expiryDate);
  const fallback = atmIv != null && atmIv > 0 ? atmIv : 0.2;
  const legs = proposedLegsToStrategyLegs(trade.legs, lotSize);
  const sigma = blendedSigmaForLegs(sigmaSmiles, legs, spot, lotSize, fallback);
  return estimateProbabilityOfProfit(spot, T, sigma, legs, lotSize);
}

/** Score trades for client-side sort; directional uses server engine_score when present. */
export function computeTradeScore(
  trade: ProposedTrade,
  pop: number | null,
): number | null {
  if (trade.status === "skipped") return null;
  if (trade.engine_score != null && Number.isFinite(trade.engine_score)) {
    return trade.engine_score;
  }
  if (pop == null || !Number.isFinite(pop)) return null;
  const prem = trade.net_premium;
  if (prem == null || !Number.isFinite(prem)) return null;
  return prem * (pop / 100);
}

export function formatConstraintViolation(
  violation: string,
  trade: ProposedTrade,
  minPopPct?: number | null,
  minAnnReturnPct?: number | null,
): string {
  if (violation === "pop_floor" && trade.pop_pct != null && minPopPct != null) {
    return `PoP ${trade.pop_pct.toFixed(1)}% (your min ${minPopPct}%)`;
  }
  if (
    violation === "min_ann_return" &&
    trade.annualized_return_pct != null &&
    minAnnReturnPct != null
  ) {
    return `ROI ${trade.annualized_return_pct.toFixed(1)}% (your min ${minAnnReturnPct}%)`;
  }
  if (violation === "infinite_loss") {
    return "Unlimited loss";
  }
  return violation.replace(/_/g, " ");
}
