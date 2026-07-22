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

/** `completed`/`reset` come straight from the backend for Group rules (the SG lifecycle
 * owns them). `exited` is a Leg·GTT-only derivation — GTT has no server-side lifecycle,
 * so the frontend still infers it from the orders. */
export type ExitRuleEffectiveStatus =
  | "armed"
  | "triggered"
  | "fired"
  | "completed"
  | "reset"
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
  /** Group rules only — per-leg error text from the broker/dispatch attempt, joined.
   * Diagnostic detail *underneath* the Reset reason, not a replacement for it: the
   * reason says what happened in the user's terms, this says what the broker said. */
  failureReason: string | null;
  /** Group rules only — the full SG record, so the shared Reset-warning copy/tiering
   * (`lib/portfolio/reset-warning`) can render without a second source of truth. */
  rule: SquareOffRuleRecord | null;
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

/**
 * For a Group rule the backend's status IS the answer — the SG lifecycle owns
 * Fired→Completed/Reset and decides it from order identity, which the frontend cannot
 * reproduce (it can't tell our own exit filling from the user squaring off manually).
 *
 * So there is deliberately no "all orders executed → exited" derivation here any more:
 * that guess would contradict the server the moment a manual fill was involved.
 *
 * `SquareOffRuleStatus` includes 'disarmed', which `fetchSquareOffRulesForExitBoard`
 * never returns (the repo query excludes it) — the exhaustive switch documents that
 * branch as unreachable rather than silently casting past it.
 */
function groupEffectiveStatus(status: SquareOffRuleStatus): ExitRuleEffectiveStatus {
  switch (status) {
    case "armed":
      return "armed";
    case "triggered":
      return "triggered";
    case "fired":
      return "fired";
    case "completed":
      return "completed";
    case "reset":
      return "reset";
    case "disarmed":
      return "reset";
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
    const effectiveStatus = groupEffectiveStatus(rule.status);
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
      resolvedAt: rule.resolved_at ?? rule.fired_at ?? null,
      orders,
      failureReason: effectiveStatus === "reset" ? buildFailureReason(rule) : null,
      rule,
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
      rule: null,
    });
  }

  return rows;
}

/** The calendar date a row resolved on as `YYYY-MM-DD`, or null when it has none.
 *
 * `resolvedAt` is a `YYYY-MM-DD HH:MM:SS` stamp straight out of SQLite, so the date is
 * simply its first ten characters. Sliced rather than parsed through `Date`, so the
 * value filtered on is character-for-character the one rendered in the Placed /
 * Resolved column — a filter that disagreed with the visible timestamp would read as a
 * bug no matter which of the two was "right". */
export function exitRuleResolvedDate(row: ExitRuleRow): string | null {
  const raw = row.resolvedAt?.trim();
  if (!raw) return null;
  const date = raw.slice(0, 10);
  return /^\d{4}-\d{2}-\d{2}$/.test(date) ? date : null;
}

/**
 * Scopes History rows to the Order Book's date range (inclusive) on resolved date, so
 * both tables on the Orders page describe the same window.
 *
 * Rows with no resolved timestamp are kept, not dropped. Only Leg·GTT rows lack one,
 * and they reach History solely by having all their spawned orders executed — those
 * orders arrive on `/book/data`'s `rule_spawned_orders`, i.e. from this very date
 * range, so such rows are already scoped by construction. Falling back to `placedAt`
 * for them would be worse than keeping them: it would hide a GTT placed weeks ago that
 * exited inside the range, which is exactly the row the user is looking for.
 */
export function filterExitRuleRowsByResolvedDate(
  rows: ExitRuleRow[],
  start: string,
  end: string,
): ExitRuleRow[] {
  return rows.filter((row) => {
    const date = exitRuleResolvedDate(row);
    if (!date) return true;
    return date >= start && date <= end;
  });
}

/** 'reset' counts as active (not History): it needs the user's attention to re-arm or
 * clean up leftover exit orders, not a status they'd only think to look for after the
 * fact. 'completed' and 'exited' are genuinely done, so they fall through to History. */
export function isExitRuleActive(status: ExitRuleEffectiveStatus): boolean {
  return (
    status === "armed" ||
    status === "triggered" ||
    status === "fired" ||
    status === "reset"
  );
}
