import { apiClient } from "@/lib/api-client";

/**
 * Per-leg GTT OCO exit orders (Portfolio > individual leg > Exit Rule).
 *
 * Unlike `squareoff-rules.ts` (a backend-persisted rule this app itself polls),
 * a GTT order is monitored entirely by ICICI once placed — there's nothing to
 * poll here; `fetchLegGttStatus` always re-queries the broker's live GTT order
 * book, filtered to the one leg.
 */

export type GttExitOrderLeg = {
  gtt_leg_type: string | null;
  action: string | null;
  trigger_price: number | null;
  limit_price: number | null;
  status: string | null;
};

export type GttExitOrderRecord = {
  gtt_order_id: string;
  gtt_type: string | null;
  order_datetime: string | null;
  legs: GttExitOrderLeg[];
};

export type PlaceGttExitOrderRequest = {
  stock_code: string;
  exchange_code: string;
  expiry_date: string;
  strike_price: string;
  right: string;
  quantity: string;
  product_type?: string;
  action: string;
  target_trigger_price: number;
  target_limit_price: number;
  stop_trigger_price: number;
  stop_limit_price: number;
};

export type LegIdentity = {
  stock_code: string;
  exchange_code: string;
  expiry_date: string;
  strike_price: string;
  right: string;
};

/** One row of the account's full GTT order book (Orders page > Profit Booking / Stop
 * Loss), unlike `GttExitOrderRecord` which is narrowed to a single leg's identity. */
export type GttExitOrderRowRecord = {
  gtt_order_id: string;
  stock_code: string | null;
  exchange_code: string | null;
  expiry_display: string | null;
  strike_price: string | null;
  right: string | null;
  quantity: string | null;
  product_type: string | null;
  gtt_type: string | null;
  order_datetime: string | null;
  legs: GttExitOrderLeg[];
  is_cancelled: boolean;
};

export async function placeLegGttExitOrder(
  body: PlaceGttExitOrderRequest,
): Promise<{ gtt_order_id: string | null; message: string | null }> {
  return apiClient.post("/portfolio/gtt-exit-orders", body);
}

export async function fetchLegGttStatus(
  leg: LegIdentity,
): Promise<GttExitOrderRecord | null> {
  const params = new URLSearchParams({
    stock_code: leg.stock_code,
    exchange_code: leg.exchange_code,
    expiry_date: leg.expiry_date,
    strike_price: leg.strike_price,
    right: leg.right,
  });
  const resp = await apiClient.get<{ order: GttExitOrderRecord | null }>(
    `/portfolio/gtt-exit-orders?${params.toString()}`,
  );
  return resp.order;
}

/** Every GTT order on the account (Orders page > Profit Booking / Stop Loss), unlike
 * `fetchLegGttStatus` which is narrowed to one leg's identity. */
export async function fetchAllGttExitOrders(): Promise<GttExitOrderRowRecord[]> {
  const resp = await apiClient.get<{ orders: GttExitOrderRowRecord[] }>(
    "/portfolio/gtt-exit-orders/all",
  );
  return resp.orders;
}

export async function cancelLegGttExitOrder(
  gttOrderId: string,
  exchangeCode: string,
): Promise<void> {
  const params = new URLSearchParams({ exchange_code: exchangeCode });
  await apiClient.delete<{ ok: boolean }>(
    `/portfolio/gtt-exit-orders/${encodeURIComponent(gttOrderId)}?${params.toString()}`,
  );
}
