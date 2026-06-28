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
      panelClassName="w-full max-w-lg rounded-xl border-2 border-red-700 bg-white p-5 shadow-2xl dark:border-red-600 dark:bg-zinc-950"
    >
      <h2
        id="breeze-api-risk-title"
        className="text-lg font-bold uppercase tracking-wide text-red-800 dark:text-red-300"
      >
        Danger: raw ICICI Breeze APIs
      </h2>
      <div className="mt-3 space-y-3 text-sm leading-relaxed text-zinc-800 dark:text-zinc-300">
        <p>
          This playground calls the <strong>live Breeze REST APIs</strong> using your broker session.
          Mistakes can have real consequences:
        </p>
        <ul className="list-disc space-y-1 pl-5 text-red-900 dark:text-red-200">
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
        <p className="text-zinc-600 dark:text-zinc-400">
          This tool is for debugging and exploration only. Use the app&apos;s order and portfolio
          screens for normal trading workflows.
        </p>
      </div>
      <div className="mt-5 flex justify-end">
        <button
          type="button"
          disabled={pending}
          onClick={onAccept}
          className="rounded-md border border-red-900 bg-red-700 px-4 py-2.5 text-sm font-semibold text-white hover:bg-red-800 disabled:opacity-60 dark:border-red-700 dark:bg-red-800 dark:hover:bg-red-900"
        >
          {pending ? "Saving…" : "I accept the risk"}
        </button>
      </div>
    </Modal>
  );
}
