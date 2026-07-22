import { apiClient } from "@/lib/api-client";

export type StrategyHedgeCandidate = {
  strike_price: number;
  right: "Call" | "Put";
  ltp: number;
  net_premium_cost: number;
  estimated_margin_relief: number;
  max_loss_estimate: number;
  score: number;
  action: "Buy";
  hedge_quantity: number;
  short_strike: number;
  hedge_type: string;
};

export type StrategyHedgeBucketSummary = {
  strategy_delta: number;
  strategy_gamma: number;
  net_premium_cash: number;
  risk_profile:
    | "net_short_call"
    | "net_short_put"
    | "multi_leg_reopt"
    | "defined";
  spot_price: number;
  days_to_expiry: number;
  lot_size: number;
};

export type StrategyHedgeSuccess = {
  summary: StrategyHedgeBucketSummary;
  candidates: StrategyHedgeCandidate[];
};

export type StrategyHedgeApiResponse = {
  Status: number;
  Error?: string | null;
  Success?: StrategyHedgeSuccess | null;
};

export type StrategyHedgeRequest = {
  stock_code: string;
  expiry_date: string;
  user_max_loss_preference: number;
  exchange_code?: string;
};

export async function postStrategyHedges(
  body: StrategyHedgeRequest,
): Promise<StrategyHedgeApiResponse> {
  return apiClient.post<StrategyHedgeApiResponse>(
    "/api/v1/hedge/strategy-options",
    body,
  );
}
