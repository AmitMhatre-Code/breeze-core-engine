"use client";

import { useState, type ReactNode } from "react";
import { usePathname } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Checkbox } from "@/components/ui/Checkbox";
import { Modal } from "@/components/ui/Modal";
import { TelegramLinkPanel } from "@/components/telegram/TelegramLinkPanel";
import { fetchAuthSession } from "@/lib/auth-session";
import { shouldFetchLicenseHomeData } from "@/lib/public-auth-routes";
import {
  fetchTelegramStatus,
  setTelegramOnboardingDismissed,
  TELEGRAM_STATUS_QUERY_KEY,
} from "@/lib/telegram/telegram-alerts";

/**
 * Post-login onboarding nudge for Telegram alerts. Modeled on
 * LoginDisclosureProvider but non-blocking: it never hides `children`, it
 * just layers a dismissible modal on top once past auth + the disclosure
 * gate. Mounted inside LoginDisclosureProvider in app-providers.tsx so it
 * only ever renders for authenticated, disclosure-acknowledged sessions.
 */
export function TelegramOnboardingProvider({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const qc = useQueryClient();
  const enabled = shouldFetchLicenseHomeData(pathname);
  const [dontShowAgain, setDontShowAgain] = useState(false);
  const [sessionDismissed, setSessionDismissed] = useState(false);

  const sessionQ = useQuery({
    queryKey: ["auth", "session"],
    queryFn: fetchAuthSession,
    enabled,
    staleTime: 30_000,
    retry: false,
  });
  const authed = Boolean(sessionQ.data?.authenticated);

  const statusQ = useQuery({
    queryKey: TELEGRAM_STATUS_QUERY_KEY,
    queryFn: fetchTelegramStatus,
    enabled: enabled && authed,
    staleTime: 10_000,
    retry: false,
  });

  const dismissMut = useMutation({
    mutationFn: setTelegramOnboardingDismissed,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: TELEGRAM_STATUS_QUERY_KEY });
    },
  });

  const status = statusQ.data;
  const shouldShow = Boolean(
    enabled &&
      authed &&
      statusQ.isSuccess &&
      status?.bot_configured &&
      !status.connected &&
      !status.onboarding_dismissed &&
      !sessionDismissed,
  );

  const close = () => {
    setSessionDismissed(true);
    if (dontShowAgain) dismissMut.mutate(true);
  };

  return (
    <>
      {children}
      <Modal
        open={shouldShow}
        onClose={close}
        titleId="telegram-onboarding-title"
        zIndexClass="z-[120]"
        panelClassName="max-h-[90vh] w-full max-w-md space-y-4 overflow-y-auto rounded-[13px] border border-border bg-elevated p-5 shadow-pop"
      >
        <div className="flex items-start justify-between gap-3">
          <div>
            <h3 id="telegram-onboarding-title" className="text-base font-semibold text-foreground">
              Get instant Stop-Loss / Profit Booking alerts
            </h3>
            <p className="mt-1 text-sm leading-relaxed text-muted">
              Connect Telegram once and get a message the moment a Profit Booking or Stop Loss
              rule fires — no need to keep this tab open.
            </p>
          </div>
          <button
            type="button"
            className="-m-1 size-9 shrink-0 rounded-lg text-xl leading-none text-muted transition hover:bg-border-soft"
            onClick={close}
            aria-label="Close"
          >
            &times;
          </button>
        </div>

        <TelegramLinkPanel onConnected={close} />

        <label className="flex items-center gap-2 text-xs text-muted">
          <Checkbox
            checked={dontShowAgain}
            onChange={setDontShowAgain}
            aria-label="Do not show this message again"
          />
          Do not show this message again
        </label>

        <div className="flex justify-end">
          <button type="button" className="app-btn-outline rounded-[8px] px-3.5 py-2 text-xs" onClick={close}>
            Maybe later
          </button>
        </div>
      </Modal>
    </>
  );
}
