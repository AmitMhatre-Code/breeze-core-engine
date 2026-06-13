"use client";

import type { ReactNode } from "react";
import { LicenseRestrictionProvider } from "@/components/license/LicenseRestrictionProvider";
import { LoginDisclosureProvider } from "@/components/login-disclosure/LoginDisclosureProvider";
import { QueryProvider } from "@/lib/query-client";
import { OrderConfirmProvider } from "@/components/order/OrderConfirmProvider";
import { RateLimitCountdownProvider } from "@/components/order/RateLimitCountdownProvider";

export function AppProviders({ children }: { children: ReactNode }) {
  return (
    <QueryProvider>
      <LicenseRestrictionProvider>
        <LoginDisclosureProvider>
          <RateLimitCountdownProvider>
            <OrderConfirmProvider>{children}</OrderConfirmProvider>
          </RateLimitCountdownProvider>
        </LoginDisclosureProvider>
      </LicenseRestrictionProvider>
    </QueryProvider>
  );
}
