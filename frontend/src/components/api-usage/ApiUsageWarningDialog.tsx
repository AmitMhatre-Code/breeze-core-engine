"use client";

import { useId, useRef } from "react";
import { Modal } from "@/components/ui/Modal";

export function ApiUsageWarningDialog(props: {
  open: boolean;
  message: string;
  onDismiss: () => void;
}) {
  const { open, message, onDismiss } = props;
  const titleId = useId();
  const closeRef = useRef<HTMLButtonElement>(null);

  return (
    <Modal
      open={open}
      onClose={onDismiss}
      titleId={titleId}
      zIndexClass="z-[100]"
      panelClassName="w-full max-w-md rounded-xl border border-amber-500/50 bg-white p-5 shadow-xl dark:border-amber-400/40 dark:bg-zinc-950"
      initialFocusRef={closeRef}
    >
      <h2
        id={titleId}
        className="text-base font-semibold text-amber-950 dark:text-amber-100"
      >
        Daily API usage warning
      </h2>
      <p className="mt-3 text-sm leading-relaxed text-zinc-700 dark:text-zinc-300">
        {message}
      </p>
      <div className="mt-5 flex justify-end">
        <button
          ref={closeRef}
          type="button"
          className="app-btn-primary rounded-lg px-4 py-2 text-sm font-medium"
          onClick={onDismiss}
        >
          Understood
        </button>
      </div>
    </Modal>
  );
}
