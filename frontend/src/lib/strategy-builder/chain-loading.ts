import type { ChainApiResponse } from "@/lib/strategy-builder/types";

export function chainIsLoading(
  stockCode: string,
  expiryDate: string,
  chainQ: {
    isPending: boolean;
    isFetching: boolean;
    data?: ChainApiResponse;
  },
): boolean {
  return Boolean(
    stockCode.trim() &&
      expiryDate.trim() &&
      (chainQ.isPending || (chainQ.isFetching && !chainQ.data)),
  );
}

export function chainIsRefreshing(chainQ: {
  isFetching: boolean;
  data?: ChainApiResponse;
}): boolean {
  return chainQ.isFetching && chainQ.data != null;
}
