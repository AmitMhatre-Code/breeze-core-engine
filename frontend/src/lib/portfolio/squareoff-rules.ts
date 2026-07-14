import { apiClient } from "@/lib/api-client";

export type SquareOffRuleStatus =
  | "armed"
  | "triggered"
  | "fired"
  | "fire_failed"
  | "disarmed";

export type SquareOffRuleLegResult = {
  scrip_key: string;
  stock_code: string;
  strike_price: string;
  right: string;
  quantity: string;
  status: "success" | "partial" | "failed";
  error?: string | null;
  order_id?: string | null;
  action?: string | null;
  price?: string | null;
};

/** One currently-open leg of an armed group rule's bucket, joined live from
 * the P&L engine's position registry (not persisted with the rule) — unlike
 * `SquareOffRuleLegResult` (only populated once the rule has fired),
 * `live_legs` is what powers the Orders page's Current MTM column. `action`
 * is the position's own entry side, not a closing order's inverted side. */
export type SquareOffRuleLiveLeg = {
  scrip_key: string;
  stock_code: string;
  strike_price: number;
  right: string;
  quantity: number;
  action: string;
  average_price: number;
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
  live_legs?: SquareOffRuleLiveLeg[] | null;
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

/** Orders page > Profit Booking / Stop Loss — includes fired/fire_failed history
 * alongside armed/triggered, unlike `fetchSquareOffRules` (Portfolio's own badge). */
export async function fetchSquareOffRulesForExitBoard(): Promise<SquareOffRuleRecord[]> {
  const resp = await apiClient.get<{ rules: SquareOffRuleRecord[] }>(
    "/portfolio/squareoff-rules/for-exit-board",
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
