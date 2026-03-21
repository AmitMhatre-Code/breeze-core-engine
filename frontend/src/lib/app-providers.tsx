"use client";

import type { ReactNode } from "react";
import { QueryProvider } from "@/lib/query-client";
import { OrderConfirmProvider } from "@/components/order/OrderConfirmProvider";

export function AppProviders({ children }: { children: ReactNode }) {
  return (
    <QueryProvider>
      <OrderConfirmProvider>{children}</OrderConfirmProvider>
    </QueryProvider>
  );
}
