import type {
  SquareOffRuleRecord,
  SquareOffRuleStatus,
} from "@/lib/portfolio/squareoff-rules";
import type { GttExitOrderRowRecord } from "@/lib/portfolio/gtt-exit-orders";

/**
 * A broker order row spawned by a Profit Booking / Stop Loss rule, as returned by
 * `/book/data`'s `rule_spawned_orders` — same shape `BookOrderRow` (orders/page.tsx)
 * already carries, plus which rule it belongs to.
 */
export type RuleSpawnedOrderRow = {
  order_id?: string;
  option?: string;
  exchange_code?: string;
  action?: string;
  quantity?: number | string;
  open_quantity?: number | string;
  price?: number | string;
  status?: string;
  cancelable?: boolean;
  modifiable?: boolean;
  pending_quantity?: number | string;
  stock_code?: string;
  expiry_date?: string;
  strike_price?: number | string;
  right?: string;
  product_type?: string;
  exit_rule_source: "squareoff_rule" | "gtt_exit_order";
  exit_rule_id: string;
  /** Group square-off rules only — which leg (scrip) within the rule this order belongs to,
   * so a leg-modify can patch just that leg's stored order_ids. */
  exit_rule_scrip_key?: string;
};

export type ExitRuleEffectiveStatus =
  | "armed"
  | "triggered"
  | "fired"
  | "fire_failed"
  | "exited";

/** One currently-open leg of a group rule's bucket, for the live-MTM overlay —
 * mirrors the backend's `SquareOffRuleLiveLeg` (joined live from the P&L
 * engine's position registry, not the rule's own fired-order storage).
 * `action` is the position's own entry side (BUY/SELL), already correct for
 * the MTM formula — no inversion needed, unlike Leg·GTT's `action`. */
export type ExitRuleLeg = {
  scripKey: string;
  strikePrice: string;
  right: string;
  action: string | null;
  quantity: string;
  averagePrice: number | null;
};

export type ExitRuleRow = {
  kind: "group" | "leg_gtt";
  id: string;
  stockCode: string;
  expiryDisplay: string;
  exchangeCode: string;
  /** Group only — number of legs the rule covers. */
  legCount: number | null;
  /** Group only — per-leg detail for the live-MTM overlay. */
  legs: ExitRuleLeg[] | null;
  /** Leg·GTT only. */
  strikePrice: string | null;
  right: string | null;
  /** Leg·GTT only — the GTT leg's *closing* side (see `ExitRuleLeg.action`), same
   * un-invert-before-use caveat applies. */
  action: string | null;
  quantity: string | null;
  /** Leg·GTT only — live entry price, straight passthrough of the backend join. */
  averagePrice: number | null;
  effectiveStatus: ExitRuleEffectiveStatus;
  /** Group: ₹ P&L target/stop. Leg·GTT: ₹ trigger price target/stop. Same shape, different unit. */
  targetValue: number | null;
  stopValue: number | null;
  /** Group only — premium offset % used to price the exit limit order. */
  targetPct: number | null;
  stopPct: number | null;
  placedAt: string | null;
  resolvedAt: string | null;
  orders: RuleSpawnedOrderRow[];
  /** Group rules only — per-leg error text from the broker/dispatch attempt, joined,
   * populated when effectiveStatus is 'fire_failed'. */
  failureReason: string | null;
};

/** Joins the per-leg error strings captured when a group rule's fire attempt fails
 * (`squareoff_dispatcher.py`'s `leg_results`) into one user-facing message. */
function buildFailureReason(rule: SquareOffRuleRecord): string | null {
  const errors = (rule.leg_results ?? [])
    .map((leg) => leg.error?.trim())
    .filter((err): err is string => !!err);
  if (errors.length === 0) return null;
  return Array.from(new Set(errors)).join(" ");
}

function isExecuted(row: RuleSpawnedOrderRow): boolean {
  return String(row.status ?? "").trim().toLowerCase().includes("execut");
}

/** `SquareOffRuleStatus` includes 'disarmed', which `fetchSquareOffRulesForExitBoard`
 * never actually returns (the repo query excludes it) — exhaustive switch documents
 * that branch as unreachable rather than silently casting past it. */
function groupEffectiveStatus(
  status: SquareOffRuleStatus,
  orders: RuleSpawnedOrderRow[],
): ExitRuleEffectiveStatus {
  if (orders.length > 0 && orders.every(isExecuted)) return "exited";
  switch (status) {
    case "armed":
      return "armed";
    case "triggered":
      return "triggered";
    case "fired":
      return "fired";
    case "fire_failed":
      return "fire_failed";
    case "disarmed":
      return "fire_failed";
  }
}

/** Leg·GTT rules never show Armed/Triggered — GTT placement is a synchronous,
 * immediately-known-outcome call (unlike Group's async poll-triggered dispatch), so
 * "Fired" is the steady-state label from the moment placement succeeds. */
function legGttEffectiveStatus(orders: RuleSpawnedOrderRow[]): ExitRuleEffectiveStatus {
  if (orders.length > 0 && orders.every(isExecuted)) return "exited";
  return "fired";
}

function parseNum(raw: unknown): number | null {
  if (raw == null || raw === "") return null;
  const n = typeof raw === "number" ? raw : Number(raw);
  return Number.isFinite(n) ? n : null;
}

/** Normalizes both rule kinds into one row shape for the Orders page > Profit
 * Booking / Stop Loss table. */
export function buildExitRuleRows(
  squareOffRules: SquareOffRuleRecord[],
  gttRows: GttExitOrderRowRecord[],
  ruleSpawnedOrders: RuleSpawnedOrderRow[],
): ExitRuleRow[] {
  const ordersByRule = new Map<string, RuleSpawnedOrderRow[]>();
  for (const row of ruleSpawnedOrders) {
    if (!row.exit_rule_id) continue;
    const key = `${row.exit_rule_source}:${row.exit_rule_id}`;
    const list = ordersByRule.get(key);
    if (list) list.push(row);
    else ordersByRule.set(key, [row]);
  }

  const rows: ExitRuleRow[] = [];

  for (const rule of squareOffRules) {
    const orders = ordersByRule.get(`squareoff_rule:${rule.id}`) ?? [];
    const effectiveStatus = groupEffectiveStatus(rule.status, orders);
    rows.push({
      kind: "group",
      id: rule.id,
      stockCode: rule.stock_code,
      expiryDisplay: rule.expiry_display,
      exchangeCode: rule.exchange_code,
      legCount: rule.leg_results?.length ?? null,
      legs: rule.live_legs
        ? rule.live_legs.map((leg) => ({
            scripKey: leg.scrip_key,
            strikePrice: String(leg.strike_price),
            right: leg.right,
            action: leg.action,
            quantity: String(leg.quantity),
            averagePrice: leg.average_price,
          }))
        : null,
      strikePrice: null,
      right: null,
      action: null,
      quantity: null,
      averagePrice: null,
      effectiveStatus,
      targetValue: rule.profit_target_pnl,
      stopValue: rule.loss_limit_pnl,
      targetPct: rule.target_premium_pct,
      stopPct: rule.stop_loss_premium_pct,
      placedAt: rule.created_at ?? null,
      resolvedAt: rule.fired_at ?? null,
      orders,
      failureReason: effectiveStatus === "fire_failed" ? buildFailureReason(rule) : null,
    });
  }

  for (const gtt of gttRows) {
    if (gtt.is_cancelled) continue;
    const orders = ordersByRule.get(`gtt_exit_order:${gtt.gtt_order_id}`) ?? [];
    const targetLeg = gtt.legs.find((l) =>
      (l.gtt_leg_type ?? "").toLowerCase().includes("target"),
    );
    const stopLeg = gtt.legs.find((l) =>
      (l.gtt_leg_type ?? "").toLowerCase().includes("stop"),
    );
    rows.push({
      kind: "leg_gtt",
      id: gtt.gtt_order_id,
      stockCode: gtt.stock_code ?? "",
      expiryDisplay: gtt.expiry_display ?? "",
      exchangeCode: gtt.exchange_code ?? "NFO",
      legCount: null,
      legs: null,
      strikePrice: gtt.strike_price,
      right: gtt.right,
      action: gtt.legs[0]?.action ?? null,
      quantity: gtt.quantity,
      averagePrice: gtt.average_price ?? null,
      effectiveStatus: legGttEffectiveStatus(orders),
      targetValue: parseNum(targetLeg?.trigger_price),
      stopValue: parseNum(stopLeg?.trigger_price),
      targetPct: null,
      stopPct: null,
      placedAt: gtt.order_datetime ?? null,
      resolvedAt: null,
      orders,
      failureReason: null,
    });
  }

  return rows;
}

/** 'fire_failed' counts as active (not History) -- it needs the user's attention to
 * retry or clean up, not a status they'd only think to look for after the fact. */
export function isExitRuleActive(status: ExitRuleEffectiveStatus): boolean {
  return (
    status === "armed" ||
    status === "triggered" ||
    status === "fired" ||
    status === "fire_failed"
  );
}
