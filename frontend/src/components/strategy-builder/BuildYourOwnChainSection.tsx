"use client";

import { useEffect, useRef } from "react";
import {
  OptionChainTable,
  scrollOptionChainAtmIntoView,
} from "@/components/order/OptionChainTable";
import type {
  ChainRow,
  ChainSuccess,
  OrderSide,
  OptionRight,
} from "@/lib/strategy-builder/types";

type BuildYourOwnChainSectionProps = {
  chainSuccess: ChainSuccess | null;
  isFetching: boolean;
  isError: boolean;
  error: Error | null;
  chainStatus: number | undefined;
  chainError: string | null | undefined;
  stockCode: string;
  expiryDate: string;
  onStrategyBuySell: (side: OrderSide, row: ChainRow, right: OptionRight) => void;
  isStrategySlotAdded: (
    strike: number,
    right: OptionRight,
    side: OrderSide,
  ) => boolean;
};

export function BuildYourOwnChainSection({
  chainSuccess,
  isFetching,
  isError,
  error,
  chainStatus,
  chainError,
  stockCode,
  expiryDate,
  onStrategyBuySell,
  isStrategySlotAdded,
}: BuildYourOwnChainSectionProps) {
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!chainSuccess?.chain_rows?.length) return;
    const t = requestAnimationFrame(() => {
      scrollOptionChainAtmIntoView(scrollRef.current);
    });
    return () => cancelAnimationFrame(t);
  }, [chainSuccess]);

  if (isFetching && !chainSuccess) {
    return (
      <p className="text-sm text-muted">
        Loading option chain…
      </p>
    );
  }

  if (isError) {
    return (
      <div className="app-alert-error text-xs">
        {error instanceof Error ? error.message : "Chain request failed"}
      </div>
    );
  }

  if (chainStatus !== undefined && chainStatus !== 200 && (chainError ?? "").trim()) {
    return (
      <div className="app-alert-error text-xs">{String(chainError).trim()}</div>
    );
  }

  if (chainSuccess?.chain_rows?.length) {
    return (
      <OptionChainTable
        chainSuccess={chainSuccess}
        scrollRef={scrollRef}
        mode="strategyBuilder"
        onStrategyBuySell={onStrategyBuySell}
        isStrategySlotAdded={isStrategySlotAdded}
      />
    );
  }

  if (!isFetching && stockCode.trim() && expiryDate.trim() && chainStatus === 200) {
    return (
      <p className="text-sm text-muted">
        No option chain rows for this expiry.
      </p>
    );
  }

  if (!stockCode.trim() || !expiryDate.trim()) {
    return (
      <p className="text-sm text-muted">
        Set underlying and expiry in section 1 to load the chain.
      </p>
    );
  }

  return null;
}
