import { apiClient } from "@/lib/api-client";

export type BreakChunkDefaultsResponse = {
  ok: boolean;
  error?: string;
  lot_size?: number;
  default_chunk_qty?: number;
  max_chunk_qty?: number;
};

export function fetchBreakChunkDefaults(args: {
  stock_code: string;
  exchange_code: string;
  expiry_date: string;
}): Promise<BreakChunkDefaultsResponse> {
  return apiClient.post<BreakChunkDefaultsResponse>(
    "/order/break-chunk-defaults",
    args,
  );
}
