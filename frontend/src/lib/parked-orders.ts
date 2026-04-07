import { apiClient } from "@/lib/api-client";
import type { OrderConfirmPayload } from "@/lib/order-confirm";

export type ParkedOrderListItem = {
  id: string;
  product_type: string;
  stock_code: string;
  exchange_code: string;
  expiry_date: string;
  right: string;
  strike_price: string;
  quantity: string;
  price: string;
  action: "Buy" | "Sell";
  chunk_qty?: string | null;
  batch_group_id?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
};

export type ParkedOrderCreateItem = {
  product_type?: string;
  stock_code: string;
  exchange_code?: string;
  expiry_date: string;
  right: string;
  strike_price: string;
  quantity: string;
  price?: string;
  action: "Buy" | "Sell";
  chunk_qty?: string;
  batch_group_id?: string;
};

export function parkedOrderToConfirmPayload(
  row: ParkedOrderListItem,
): OrderConfirmPayload {
  return {
    product_type: row.product_type || "Options",
    stock_code: row.stock_code,
    exchange_code: row.exchange_code || "NFO",
    expiry_date: row.expiry_date,
    right: row.right,
    strike_price: row.strike_price,
    quantity: row.quantity,
    price: row.price || "0",
    action: row.action,
  };
}

export async function fetchParkedOrders(): Promise<ParkedOrderListItem[]> {
  const r = await apiClient.get<{ orders: ParkedOrderListItem[] }>(
    "/book/parked-orders",
  );
  return r.orders ?? [];
}

export async function createParkedOrders(body: {
  items: ParkedOrderCreateItem[];
  replace_ids?: string[];
}): Promise<void> {
  await apiClient.post("/book/parked-orders", body);
}

export async function patchParkedOrder(
  id: string,
  body: { quantity?: string; price?: string; chunk_qty?: string | null },
): Promise<ParkedOrderListItem> {
  return apiClient.patch<ParkedOrderListItem>(
    `/book/parked-orders/${encodeURIComponent(id)}`,
    body,
  );
}

export async function deleteParkedOrder(id: string): Promise<void> {
  await apiClient.delete(`/book/parked-orders/${encodeURIComponent(id)}`);
}

export async function deleteParkedOrdersMany(ids: string[]): Promise<number> {
  const r = await apiClient.post<{ deleted: number }>(
    "/book/parked-orders/delete-many",
    { ids },
  );
  return r.deleted ?? 0;
}
