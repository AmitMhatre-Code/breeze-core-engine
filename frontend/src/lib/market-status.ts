import { apiClient } from "@/lib/api-client";

export type MarketStatus = {
  is_open: boolean;
  closed_reason: string;
};

export async function fetchMarketStatus(): Promise<MarketStatus> {
  return apiClient.get<MarketStatus>("/api/settings/market-status");
}
