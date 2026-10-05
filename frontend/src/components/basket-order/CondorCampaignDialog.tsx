"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { ExecutionView } from "@/components/condor/AdjustTicket";
import type { CondorBotConfig } from "@/components/bots/CondorSettings";
import { Modal } from "@/components/ui/Modal";
import { formatInr, orderLine, useExecution, useOpenCampaign, type TicketRow } from "@/lib/condor";
import { BOT_DYNAMIC_CONDOR, useBots } from "@/lib/use-bots";

/**
 * Basket Orders with "Manage as a Dynamic Iron Condor campaign" ticked (#67).
 *
 * The basket is not placed the basket's way. It becomes a new campaign's first tranche and goes
 * through the campaign executor: one order at a time, wings first, each at a bounded limit at
 * the live touch, every fill booked to the campaign's ledger as it lands. A refusal -- the
 * expiry already managed, legs already held there, a short no wing caps -- comes back before
 * any order is sent, and leaves no campaign behind.
 */
export function CondorCampaignDialog({
  open,
  onClose,
  expiry,
  orders,
}: {
  open: boolean;
  onClose: () => void;
  expiry: string;
  orders: TicketRow[];
}) {
  const router = useRouter();
  const bots = useBots();
  const settings = (bots.data?.find((b) => b.bot_type === BOT_DYNAMIC_CONDOR)?.config as unknown as CondorBotConfig | undefined)
    ?.campaign;
  const openCampaign = useOpenCampaign();
  const [started, setStarted] = useState<{ campaignId: string; executionId: string } | null>(null);
  const execution = useExecution(started?.campaignId ?? "", started?.executionId ?? null);
  const exec = execution.data ?? null;
  const running = Boolean(started) && (!exec || exec.status === "running");
  // Wings first, as the executor sends them.
  const sequence = [...orders].sort((a, b) => (a.action === b.action ? 0 : a.action === "Buy" ? -1 : 1));

  const close = () => {
    if (running) return;
    setStarted(null);
    openCampaign.reset();
    onClose();
  };

  return (
    <Modal
      open={open}
      onClose={close}
      pending={openCampaign.isPending || running}
      titleId="condor-campaign-dialog-title"
      zIndexClass="z-[110]"
      panelClassName="w-full max-w-lg rounded-xl border border-border bg-panel p-5 shadow-pop"
    >
      <h2 id="condor-campaign-dialog-title" className="app-text-heading">
        Start a Dynamic Iron Condor campaign · NIFTY {expiry}
      </h2>

      {started ? (
        <div className="mt-4 space-y-3">
          <ExecutionView exec={exec} />
          <div className="flex justify-end gap-2">
            <button type="button" className="app-btn-secondary" onClick={close} disabled={running}>
              Close
            </button>
            <button
              type="button"
              className="app-btn-primary"
              disabled={running}
              onClick={() => {
                close();
                router.push("/portfolio");
              }}
            >
              Open its card on Portfolio
            </button>
          </div>
        </div>
      ) : (
        <>
          <p className="mt-1 text-xs leading-relaxed text-muted">
            This basket becomes the campaign&rsquo;s first tranche. The campaign executor places it one order at a time,
            wings first, each at a bounded limit at the live bid or ask &mdash; not at the prices in the basket &mdash; and
            books every fill to the campaign&rsquo;s ledger. A step that does not fill stops the rest, and the position
            stays hedged.
          </p>
          <ol className="mt-3 space-y-0.5 rounded-lg border border-border bg-panel2 p-3 font-mono text-xs">
            {sequence.map((o, i) => (
              <li key={`${o.action}-${o.strike}-${o.right}`}>
                {i + 1}. {orderLine({ ...o, opening: true, price: null })}
              </li>
            ))}
          </ol>
          <p className="mt-3 text-hint leading-relaxed text-faint">
            Managed on the Dynamic Iron Condor bot&rsquo;s campaign settings
            {settings
              ? ` (${settings.tranches} tranche(s), margin ceiling ${formatInr(settings.margin_ceiling_inr)}, exit at ${settings.exit_dte} DTE)`
              : ""}
            : change them with the gear on its card. Its card on Portfolio then suggests the remaining tranches, rolls
            and the exit at the two daily checks. It is your campaign, not the bot&rsquo;s.
          </p>
          {openCampaign.error ? (
            <p className="mt-2 text-xs text-down" role="alert">
              {openCampaign.error instanceof Error ? openCampaign.error.message : "Could not start the campaign."}
            </p>
          ) : null}
          <div className="mt-4 flex justify-end gap-2">
            <button type="button" className="app-btn-secondary" onClick={close} disabled={openCampaign.isPending}>
              Cancel
            </button>
            <button
              type="button"
              className="app-btn-primary"
              disabled={openCampaign.isPending || !orders.length}
              onClick={() =>
                openCampaign.mutate(
                  { expiry, orders },
                  { onSuccess: (r) => setStarted({ campaignId: r.campaign.id, executionId: r.execution.id }) },
                )
              }
            >
              {openCampaign.isPending ? "Starting…" : "Start campaign and place"}
            </button>
          </div>
        </>
      )}
    </Modal>
  );
}
