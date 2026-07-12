"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { Modal } from "@/components/ui/Modal";
import { TelegramLinkPanel } from "@/components/telegram/TelegramLinkPanel";
import { fetchTelegramStatus, TELEGRAM_STATUS_QUERY_KEY } from "@/lib/telegram/telegram-alerts";

/**
 * Inline warning shown while arming a rule whose fire *does* dispatch a
 * Telegram alert (group square-off rules only) when the user hasn't linked
 * an account yet. Clicking it opens the link/QR modal without leaving the
 * rule form.
 */
export function TelegramNudgeBanner() {
  const [modalOpen, setModalOpen] = useState(false);

  const statusQ = useQuery({
    queryKey: TELEGRAM_STATUS_QUERY_KEY,
    queryFn: fetchTelegramStatus,
    staleTime: 10_000,
  });

  const status = statusQ.data;
  if (!statusQ.isSuccess || !status?.bot_configured || status.connected) return null;

  return (
    <>
      <button
        type="button"
        className="w-full rounded-md border border-amber-accent/40 bg-amber-tint px-3 py-2 text-left text-xs leading-relaxed text-amber-accent transition hover:brightness-[1.03]"
        onClick={() => setModalOpen(true)}
      >
        ⚠️ You won&rsquo;t receive real-time alerts for this order.{" "}
        <span className="font-semibold underline">Click here to link Telegram</span>
      </button>

      <Modal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        titleId="telegram-nudge-title"
        zIndexClass="z-[130]"
        panelClassName="max-h-[90vh] w-full max-w-sm space-y-4 overflow-y-auto rounded-[13px] border border-border bg-elevated p-5 shadow-pop"
      >
        <div className="flex items-start justify-between gap-3">
          <h3 id="telegram-nudge-title" className="text-base font-semibold text-foreground">
            Link Telegram for alerts
          </h3>
          <button
            type="button"
            className="-m-1 size-9 shrink-0 rounded-lg text-xl leading-none text-muted transition hover:bg-border-soft"
            onClick={() => setModalOpen(false)}
            aria-label="Close"
          >
            &times;
          </button>
        </div>
        <TelegramLinkPanel onConnected={() => setModalOpen(false)} />
      </Modal>
    </>
  );
}
