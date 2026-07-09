"use client";

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { useLicenseRestrictions } from "@/components/license/LicenseRestrictionProvider";
import {
  OrderExecutionConfirmDialog,
  type ExecutionPreviewLeg,
} from "@/components/shared/order/OrderExecutionConfirmDialog";
import type { OrderConfirmPayload } from "@/lib/order-confirm";
import type { OptionRight, QuoteMeta } from "@/lib/strategy-builder/types";

export type OpenOrderConfirmOptions = {
  /** When executing from parked rows, remove them after successful place */
  sourceParkedIds?: string[];
  quoteMeta?: QuoteMeta | null;
};

export type OpenExecutionConfirmArgs = {
  stockCode: string;
  exchangeCode: string;
  expiryDisplay: string;
  legs: ExecutionPreviewLeg[];
  sourceParkedIds?: string[];
  productType?: string;
  quoteMeta?: QuoteMeta | null;
};

type OrderConfirmContextValue = {
  openOrderConfirm: (
    payload: OrderConfirmPayload,
    opts?: OpenOrderConfirmOptions,
  ) => void;
  openExecutionConfirm: (args: OpenExecutionConfirmArgs) => void;
};

const OrderConfirmContext = createContext<OrderConfirmContextValue | null>(
  null,
);

export function useOrderConfirm(): OrderConfirmContextValue {
  const ctx = useContext(OrderConfirmContext);
  if (!ctx) {
    throw new Error("useOrderConfirm must be used within OrderConfirmProvider");
  }
  return ctx;
}

type ConfirmModalState =
  | { open: false }
  | {
      open: true;
      mode: "single";
      base: OrderConfirmPayload;
      sourceParkedIds: string[];
      quoteMeta: QuoteMeta | null;
    }
  | {
      open: true;
      mode: "multi";
      stockCode: string;
      exchangeCode: string;
      expiryDisplay: string;
      legs: ExecutionPreviewLeg[];
      sourceParkedIds: string[];
      productType: string;
      quoteMeta: QuoteMeta | null;
    };

function orderPayloadToLeg(base: OrderConfirmPayload): ExecutionPreviewLeg {
  const strike = parseFloat(String(base.strike_price).replace(/,/g, ""));
  const q = parseInt(String(base.quantity).replace(/,/g, ""), 10);
  const prem = parseFloat(String(base.price).replace(/,/g, ""));
  const r = base.right.trim();
  const right: OptionRight =
    r.toLowerCase().startsWith("p") || r.toUpperCase() === "PE"
      ? "Put"
      : "Call";
  return {
    strike: Number.isFinite(strike) ? Math.round(strike) : 0,
    right,
    side: base.action,
    quantity: Number.isFinite(q) && q > 0 ? q : 0,
    premiumPerUnit: Number.isFinite(prem) && prem >= 0 ? prem : 0,
    aggressiveLimit: base.aggressive_limit ?? false,
  };
}

export function OrderConfirmProvider({ children }: { children: ReactNode }) {
  const { guardTradingAction } = useLicenseRestrictions();
  const [modal, setModal] = useState<ConfirmModalState>({ open: false });

  const close = useCallback(() => {
    setModal({ open: false });
  }, []);

  const openOrderConfirm = useCallback(
    (payload: OrderConfirmPayload, opts?: OpenOrderConfirmOptions) => {
      guardTradingAction(() => {
        setModal({
          open: true,
          mode: "single",
          base: payload,
          sourceParkedIds: [...(opts?.sourceParkedIds ?? [])],
          quoteMeta: opts?.quoteMeta ?? null,
        });
      });
    },
    [guardTradingAction],
  );

  const openExecutionConfirm = useCallback(
    (args: OpenExecutionConfirmArgs) => {
      guardTradingAction(() => {
        setModal({
          open: true,
          mode: "multi",
          stockCode: args.stockCode,
          exchangeCode: args.exchangeCode,
          expiryDisplay: args.expiryDisplay,
          legs: args.legs,
          sourceParkedIds: [...(args.sourceParkedIds ?? [])],
          productType: args.productType ?? "Options",
          quoteMeta: args.quoteMeta ?? null,
        });
      });
    },
    [guardTradingAction],
  );

  const value = useMemo(
    () => ({ openOrderConfirm, openExecutionConfirm }),
    [openOrderConfirm, openExecutionConfirm],
  );

  return (
    <OrderConfirmContext.Provider value={value}>
      {children}
      {modal.open && modal.mode === "single" ? (
        <OrderExecutionConfirmDialog
          open
          onClose={close}
          stockCode={modal.base.stock_code}
          exchangeCode={modal.base.exchange_code || "NFO"}
          expiryDisplay={modal.base.expiry_date}
          legs={[orderPayloadToLeg(modal.base)]}
          sourceParkedIds={modal.sourceParkedIds}
          productType={modal.base.product_type || "Options"}
          quoteMeta={modal.quoteMeta}
        />
      ) : modal.open && modal.mode === "multi" ? (
        <OrderExecutionConfirmDialog
          open
          onClose={close}
          stockCode={modal.stockCode}
          exchangeCode={modal.exchangeCode}
          expiryDisplay={modal.expiryDisplay}
          legs={modal.legs}
          sourceParkedIds={modal.sourceParkedIds}
          productType={modal.productType}
          quoteMeta={modal.quoteMeta}
        />
      ) : null}
    </OrderConfirmContext.Provider>
  );
}
