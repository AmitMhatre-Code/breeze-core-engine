import { apiClient } from "@/lib/api-client";

export type SquareOffRuleStatus = "armed" | "fired" | "fire_failed" | "disarmed";

export type SquareOffRuleLegResult = {
  scrip_key: string;
  stock_code: string;
  strike_price: string;
  right: string;
  quantity: string;
  status: "success" | "failed";
  error?: string | null;
};

export type SquareOffRuleRecord = {
  id: string;
  stock_code: string;
  expiry_display: string;
  exchange_code: string;
  profit_target_pnl: number;
  loss_limit_pnl: number;
  target_premium_pct: number;
  stop_loss_premium_pct: number;
  status: SquareOffRuleStatus;
  leg_results?: SquareOffRuleLegResult[] | null;
  created_at?: string | null;
  fired_at?: string | null;
};

export type ArmSquareOffRuleRequest = {
  stock_code: string;
  expiry_date: string;
  exchange_code?: string;
  profit_target_pnl: number;
  loss_limit_pnl: number;
  target_premium_pct: number;
  stop_loss_premium_pct: number;
};

export const SQUAREOFF_RULES_QUERY_KEY = ["portfolio", "squareoff-rules"] as const;

export async function fetchSquareOffRules(): Promise<SquareOffRuleRecord[]> {
  const resp = await apiClient.get<{ rules: SquareOffRuleRecord[] }>(
    "/portfolio/squareoff-rules",
  );
  return resp.rules;
}

export async function armSquareOffRule(
  body: ArmSquareOffRuleRequest,
): Promise<SquareOffRuleRecord> {
  return apiClient.post<SquareOffRuleRecord>("/portfolio/squareoff-rules", body);
}

export async function disarmSquareOffRule(ruleId: string): Promise<void> {
  await apiClient.delete<{ ok: boolean }>(
    `/portfolio/squareoff-rules/${encodeURIComponent(ruleId)}`,
  );
}

/** Same (stock_code, expiry_display) bucket Hedge/Square Off All group by. */
export function squareOffRuleGroupKey(stockCode: string, expiryDisplay: string): string {
  return `${stockCode.trim().toUpperCase()}|${expiryDisplay.trim()}`;
}
