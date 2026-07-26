import { apiClient } from "@/lib/api-client";

/**
 * Execution style for the aggressive-order (⚡) toggle:
 *  - "market": native ICICI market order (order_type=market, price=0). May be rejected upstream
 *    until ICICI enables native market orders.
 *  - "limit_tolerance": an ordinary limit order priced off LTP by a tolerance % (server-derived).
 *    Works today with no ICICI dependency.
 */
export type AggressiveOrderMode = "market" | "limit_tolerance";

export type AggressiveOrderPreferences = {
  user_id: string;
  enabled: boolean;
  mode: AggressiveOrderMode;
  tolerance_pct: number;
  default_tolerance_pct: number;
  max_tolerance_pct: number;
};

export async function getAggressiveOrderPreferences(): Promise<AggressiveOrderPreferences> {
  return apiClient.get<AggressiveOrderPreferences>(
    "/api/settings/aggressive-order/preferences",
  );
}

export async function setAggressiveOrderPreferences(body: {
  mode?: AggressiveOrderMode;
  tolerance_pct?: number;
}): Promise<AggressiveOrderPreferences> {
  return apiClient.post<AggressiveOrderPreferences>(
    "/api/settings/aggressive-order/preferences",
    body,
  );
}

export type AggressivePriceLeg = {
  ref: string;
  stock_code: string;
  exchange_code: string;
  expiry_date: string;
  right: string;
  strike_price: string;
  action: "Buy" | "Sell";
};

export type AggressivePriceResultItem = {
  ref: string;
  price?: string | null;
  ltp?: number | null;
  error?: string | null;
};

export type AggressivePriceResponse = {
  tolerance_pct: number;
  results: AggressivePriceResultItem[];
};

/**
 * Ask the backend to derive tick-rounded aggressive limit prices from live LTP (fetched
 * server-side). Returns a ref -> result map so multi-leg callers can plug each price back in.
 */
export async function resolveAggressivePrices(
  legs: AggressivePriceLeg[],
  tolerancePct: number,
): Promise<Map<string, AggressivePriceResultItem>> {
  const resp = await apiClient.post<AggressivePriceResponse>(
    "/order/aggressive-price",
    { legs, tolerance_pct: tolerancePct },
  );
  return new Map(resp.results.map((r) => [r.ref, r]));
}
