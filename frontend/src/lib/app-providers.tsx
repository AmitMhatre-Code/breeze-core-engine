"use client";

import type { ReactNode } from "react";
import { LicenseRestrictionProvider } from "@/components/license/LicenseRestrictionProvider";
import { LoginDisclosureProvider } from "@/components/login-disclosure/LoginDisclosureProvider";
import { QueryProvider } from "@/lib/query-client";
import { OrderConfirmProvider } from "@/components/order/OrderConfirmProvider";

export function AppProviders({ children }: { children: ReactNode }) {
  return (
    <QueryProvider>
      <LicenseRestrictionProvider>
        <LoginDisclosureProvider>
          <OrderConfirmProvider>{children}</OrderConfirmProvider>
        </LoginDisclosureProvider>
      </LicenseRestrictionProvider>
    </QueryProvider>
  );
}
