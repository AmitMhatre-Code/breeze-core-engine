"use client";

import type { ReactNode } from "react";
import { HelpProvider } from "@/lib/help/help-context";
import { LicenseRestrictionProvider } from "@/components/license/LicenseRestrictionProvider";
import { LoginDisclosureProvider } from "@/components/login-disclosure/LoginDisclosureProvider";
import { QueryProvider } from "@/lib/query-client";
import { OrderConfirmProvider } from "@/components/shared/order/OrderConfirmProvider";
import { RateLimitCountdownProvider } from "@/components/order/RateLimitCountdownProvider";
import { TelegramOnboardingProvider } from "@/components/telegram/TelegramOnboardingProvider";

export function AppProviders({ children }: { children: ReactNode }) {
  return (
    <QueryProvider>
      <HelpProvider>
        <LicenseRestrictionProvider>
          <LoginDisclosureProvider>
            <TelegramOnboardingProvider>
              <RateLimitCountdownProvider>
                <OrderConfirmProvider>{children}</OrderConfirmProvider>
              </RateLimitCountdownProvider>
            </TelegramOnboardingProvider>
          </LoginDisclosureProvider>
        </LicenseRestrictionProvider>
      </HelpProvider>
    </QueryProvider>
  );
}
