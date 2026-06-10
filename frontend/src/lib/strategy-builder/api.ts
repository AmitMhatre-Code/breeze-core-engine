import { apiClient } from "@/lib/api-client";
import type { ProposeTradesApiResponse } from "@/lib/strategy-builder/types";

export type ProposeTradesParams = {
  exchange_code: string;
  stock_code: string;
  expiry_date: string;
  range_lower: number;
  range_upper: number;
  margin_lacs: number;
  max_loss_lacs: number;
  provision_elm: boolean;
};

export async function proposeTrades(
  params: ProposeTradesParams,
): Promise<ProposeTradesApiResponse> {
  return apiClient.post<ProposeTradesApiResponse>(
    "/strategy-builder/propose-trades",
    params,
  );
}
