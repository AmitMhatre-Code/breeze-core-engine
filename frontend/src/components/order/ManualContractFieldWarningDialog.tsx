"use client";

import { useEffect } from "react";
import { sb } from "@/lib/strategy-builder/ui";

type ManualContractField = "expiry" | "strike";

type Props = {
  open: boolean;
  field: ManualContractField;
  onCancel: () => void;
  onConfirm: () => void;
};

const FIELD_LABEL: Record<ManualContractField, string> = {
  expiry: "expiry date",
  strike: "strike price",
};

export function ManualContractFieldWarningDialog({
  open,
  field,
  onCancel,
  onConfirm,
}: Props) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onCancel]);

  if (!open) return null;

  const label = FIELD_LABEL[field];

  return (
    <div
      className="fixed inset-0 z-[120] flex items-center justify-center bg-black/50 p-4 dark:bg-black/60"
      role="presentation"
      onClick={onCancel}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="manual-contract-field-warning-title"
        className={`${sb.modalPanel} w-full max-w-md`}
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => e.stopPropagation()}
      >
        <h3
          id="manual-contract-field-warning-title"
          className="text-base font-semibold text-amber-800 dark:text-amber-400"
        >
          Manual entry warning
        </h3>
        <p className="text-sm leading-relaxed text-zinc-600 dark:text-zinc-400">
          Manually editing the {label} instead of choosing from the dropdown may
          lead to unpredictable trade transactions. The broker may reject the
          order or fill an unintended contract.
        </p>
        <div className="flex justify-end gap-2 pt-1">
          <button type="button" className={sb.btnSecondary} onClick={onCancel}>
            Cancel
          </button>
          <button type="button" className={sb.btnDanger} onClick={onConfirm}>
            I understand — edit manually
          </button>
        </div>
      </div>
    </div>
  );
}
