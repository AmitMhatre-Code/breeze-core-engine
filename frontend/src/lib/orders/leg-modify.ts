/** One order in a leg-modify target, as far as the open-quantity/parsing helpers
 * below need to know about it — structurally compatible with both `BookOrderRow`
 * and `RuleSpawnedOrderRow` (orders/page.tsx), so no import back into that file
 * is needed. */
export type LegModifyableOrder = {
  order_id?: string;
  pending_quantity?: number | string;
  modifiable?: boolean;
};

function toIntOrZero(raw: number | string | undefined): number {
  if (raw == null) return 0;
  const n = typeof raw === "number" ? raw : parseInt(String(raw), 10);
  return Number.isFinite(n) ? n : 0;
}

/** Sum of not-yet-filled quantity across only the leg's open/modifiable orders —
 * the "current quantity" a leg-modify should be seeded with. Excludes cancelled,
 * expired, and executed orders, whose quantity can never be changed. */
export function legOpenQuantity(orders: LegModifyableOrder[]): number {
  return orders.reduce(
    (sum, o) => (o.modifiable ? sum + toIntOrZero(o.pending_quantity) : sum),
    0,
  );
}

/** Parses a non-negative integer string. Unlike parsePositiveInt (orders/page.tsx),
 * 0 is valid — used for the leg-modify open quantity, where 0 means "cancel all
 * remaining open orders for this leg." */
export function parseNonNegativeInt(raw: string): number | null {
  const n = parseInt(raw.trim(), 10);
  if (!Number.isFinite(n) || n < 0) return null;
  return n;
}
