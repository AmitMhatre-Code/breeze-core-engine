"use client";

import { Modal } from "@/components/ui/Modal";

type Props = {
  open: boolean;
  pending: boolean;
  onAccept: () => void;
};

export function BreezeApiRiskGateDialog({ open, pending, onAccept }: Props) {
  return (
    <Modal
      open={open}
      onClose={() => {}}
      dismissible={false}
      pending={pending}
      titleId="breeze-api-risk-title"
      zIndexClass="z-[120]"
      panelClassName="w-full max-w-lg rounded-xl border-2 border-down bg-panel p-5 shadow-pop"
    >
      <h2
        id="breeze-api-risk-title"
        className="text-lg font-bold uppercase tracking-wide text-down"
      >
        Danger: raw ICICI Breeze APIs
      </h2>
      <div className="mt-3 space-y-3 text-sm leading-relaxed text-foreground">
        <p>
          This playground calls the <strong>live Breeze REST APIs</strong> using your broker session.
          Mistakes can have real consequences:
        </p>
        <ul className="list-disc space-y-1 pl-5 text-down">
          <li>
            <strong>Place, modify, cancel, or square off</strong> orders unintentionally
          </li>
          <li>
            <strong>Move or allocate funds</strong> between segments via set_funds
          </li>
          <li>
            Trigger <strong>GTT orders</strong> that execute when conditions are met
          </li>
          <li>
            Consume your <strong>daily ICICI API quota</strong> (5,000 calls/day)
          </li>
        </ul>
        <p className="text-muted">
          This tool is for debugging and exploration only. Use the app&apos;s order and portfolio
          screens for normal trading workflows.
        </p>
      </div>
      <div className="mt-5 flex justify-end">
        <button
          type="button"
          disabled={pending}
          onClick={onAccept}
          className="inline-flex items-center justify-center rounded-lg bg-down-btn px-4 py-2.5 text-sm font-bold text-down-ink transition hover:brightness-[1.06] focus:outline-none focus-visible:ring-2 focus-visible:ring-down/40 disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50"
        >
          {pending ? "Saving…" : "I accept the risk"}
        </button>
      </div>
    </Modal>
  );
}
