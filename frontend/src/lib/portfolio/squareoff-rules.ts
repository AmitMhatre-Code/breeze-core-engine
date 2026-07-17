import { apiClient } from "@/lib/api-client";

/** Strategy Group lifecycle. `triggered` is an internal sub-second transient (guards
 * double-fire) and renders as Armed. `fire_failed` is retired — a placement failure is
 * one flavour of `reset`, which always carries a `reset_reason`. */
export type SquareOffRuleStatus =
  | "armed"
  | "triggered"
  | "fired"
  | "completed"
  | "reset"
  | "disarmed";

/**
 * How dangerous a Reset actually is, derived server-side from the live order book +
 * positions (never stored, and never recomputed here — the backend owns the join).
 *
 *   settled     — no live orphaned exit orders; nothing at stake
 *   orders_live — live orphans that still correctly close open legs: automation the
 *                 user thinks is off will still act, but in the direction they intended
 *   contra_risk — an orphan whose leg is already closed: filling OPENS a new position
 *
 * Tier 3 is not a louder tier 2 — different verb, different stake.
 */
export type ResetHazardTier = "settled" | "orders_live" | "contra_risk";

/** One of a Reset SG's exit orders still working at the exchange. Reset withdraws future
 * automation; it does not retract orders already placed, so these keep going. */
export type SquareOffRuleOrphanOrder = {
  order_id: string;
  stock_code: string;
  strike_price: string;
  right: string;
  action: string;
  quantity: string;
  price?: string | null;
  /** The leg is already closed — if this fills it will OPEN a new position. */
  opens_contra_position: boolean;
};

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
  resolved_at?: string | null;
  /** Why monitoring stopped, in the user's own terms. Always set on a Reset. */
  reset_reason?: string | null;
  /** Reset rows only — derived server-side. */
  hazard_tier?: ResetHazardTier | null;
  orphan_orders?: SquareOffRuleOrphanOrder[] | null;
  /** True while any orphan is live. Blocks BOTH re-arming (a new fire would stack a
   * duplicate exit on a resting one) and dismissal (the UI must not be able to hide
   * live risk). */
  rearm_blocked?: boolean;
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

export type CancelOrphanOrdersResult = {
  ok: boolean;
  cancelled: string[];
  failed: { order_id: string; error: string }[];
};

/** Cancel a Reset SG's still-live exit orders. User-initiated only — Reset itself never
 * retracts orders already placed (cancelling a working stop-loss exit because an
 * unrelated leg failed would leave the user unprotected mid-move). */
export async function cancelSquareOffOrphanOrders(
  ruleId: string,
): Promise<CancelOrphanOrdersResult> {
  return apiClient.post<CancelOrphanOrdersResult>(
    `/portfolio/squareoff-rules/${encodeURIComponent(ruleId)}/cancel-orphan-orders`,
    {},
  );
}

/** Same (stock_code, expiry_display) bucket Hedge/Square Off All group by. */
export function squareOffRuleGroupKey(stockCode: string, expiryDisplay: string): string {
  return `${stockCode.trim().toUpperCase()}|${expiryDisplay.trim()}`;
}
