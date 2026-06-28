"use client";

import { sb } from "@/lib/strategy-builder/ui";
import { Modal } from "@/components/ui/Modal";

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
  const label = FIELD_LABEL[field];

  return (
    <Modal
      open={open}
      onClose={onCancel}
      titleId="manual-contract-field-warning-title"
      zIndexClass="z-[120]"
      panelClassName={`${sb.modalPanel} w-full max-w-md`}
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
    </Modal>
  );
}
