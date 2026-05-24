"use client";

import { type ReactNode, useCallback } from "react";
import { useLicenseRestrictions } from "@/components/license/LicenseRestrictionProvider";

function isBlockedInteractiveTarget(target: EventTarget | null): boolean {
  if (!(target instanceof Element)) return false;
  const el = target.closest(
    "button, [role='button'], input[type='submit'], input[type='button']",
  );
  if (!el) return false;
  if (el.hasAttribute("data-license-allow")) return false;
  if (el.closest("[data-license-allow]")) return false;
  return true;
}

export function RevokedTradingPageGuard({ children }: { children: ReactNode }) {
  const { tradingReadOnly, showRevokedDialog } = useLicenseRestrictions();

  const onClickCapture = useCallback(
    (event: React.MouseEvent) => {
      if (!tradingReadOnly) return;
      if (!isBlockedInteractiveTarget(event.target)) return;
      event.preventDefault();
      event.stopPropagation();
      showRevokedDialog();
    },
    [tradingReadOnly, showRevokedDialog],
  );

  if (!tradingReadOnly) {
    return children;
  }

  return <div onClickCapture={onClickCapture}>{children}</div>;
}
