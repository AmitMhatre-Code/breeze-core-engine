"use client";

import { useQuery } from "@tanstack/react-query";
import { useCallback, useMemo, useState } from "react";
import { fetchBreakChunkDefaults } from "@/lib/break-chunk-defaults";

/**
 * Fetches freeze-aligned default chunk size from the backend and keeps an optional
 * user edit scoped to the current contract (stock / exchange / expiry).
 */
export function useBreakChunkQty(opts: {
  stockCode: string;
  exchangeCode: string;
  expiryDisplay: string;
  enabled: boolean;
}) {
  const { stockCode, exchangeCode, expiryDisplay, enabled } = opts;

  const contractKey = useMemo(
    () =>
      `${stockCode.trim()}|${exchangeCode.trim()}|${expiryDisplay.trim()}`,
    [stockCode, exchangeCode, expiryDisplay],
  );

  const defaultsQuery = useQuery({
    queryKey: [
      "order",
      "break-chunk-defaults",
      stockCode,
      exchangeCode,
      expiryDisplay,
    ],
    queryFn: () =>
      fetchBreakChunkDefaults({
        stock_code: stockCode.trim(),
        exchange_code: exchangeCode.trim() || "NFO",
        expiry_date: expiryDisplay.trim(),
      }),
    enabled:
      enabled && Boolean(stockCode.trim() && expiryDisplay.trim()),
    staleTime: 60_000,
  });

  const serverDefaultStr = useMemo(() => {
    const d = defaultsQuery.data;
    if (
      d?.ok === true &&
      d.default_chunk_qty != null &&
      Number.isFinite(d.default_chunk_qty)
    ) {
      return String(d.default_chunk_qty);
    }
    return "";
  }, [defaultsQuery.data]);

  const [overrideByKey, setOverrideByKey] = useState<{
    key: string;
    value: string;
  } | null>(null);

  const chunkQty =
    overrideByKey?.key === contractKey ? overrideByKey.value : serverDefaultStr;

  const setChunkQty = useCallback(
    (value: string) => {
      setOverrideByKey({ key: contractKey, value });
    },
    [contractKey],
  );

  const chunkReady =
    defaultsQuery.isSuccess && defaultsQuery.data?.ok === true;

  return { chunkQty, setChunkQty, defaultsQuery, chunkReady };
}
