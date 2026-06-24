"use client";

import { PopHelpTrigger } from "@/components/strategy-builder/PopHelpTrigger";
import { POP_LABELS, type PopLabelVariant } from "@/lib/strategy-builder/pop-help";

type Props = {
  variant: PopLabelVariant;
  showInfo?: boolean;
  className?: string;
};

const DEFAULT_SHOW_INFO: Record<PopLabelVariant, boolean> = {
  field: true,
  inline: true,
  metric: true,
  sort: false,
};

export function PopLabel({ variant, showInfo, className }: Props) {
  const label = POP_LABELS[variant];
  const withInfo = showInfo ?? DEFAULT_SHOW_INFO[variant];

  return (
    <span className={`inline-flex items-center gap-1 ${className ?? ""}`}>
      <span>{label}</span>
      {withInfo ? <PopHelpTrigger /> : null}
    </span>
  );
}
