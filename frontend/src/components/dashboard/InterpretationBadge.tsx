import type { CSSProperties } from "react";
import type { InterpretTone } from "@/lib/dashboard-interpretation";

/** Inline colours so chips stay filled in high-contrast / forced-colour modes and any CSS order bugs. */
const TONE_STYLE: Record<InterpretTone, CSSProperties> = {
  positive: {
    backgroundColor: "#059669",
    color: "#ffffff",
    border: "1px solid rgba(16, 185, 129, 0.65)",
    forcedColorAdjust: "none",
  },
  muted: {
    backgroundColor: "#fbbf24",
    color: "#422006",
    border: "1px solid rgba(245, 158, 11, 0.75)",
    forcedColorAdjust: "none",
  },
  caution: {
    backgroundColor: "#d97706",
    color: "#fffbeb",
    border: "1px solid rgba(217, 119, 6, 0.9)",
    forcedColorAdjust: "none",
  },
  alarm: {
    backgroundColor: "#dc2626",
    color: "#ffffff",
    border: "1px solid rgba(220, 38, 38, 0.85)",
    forcedColorAdjust: "none",
  },
};

export function InterpretationBadge({
  label,
  tooltip,
  tone,
}: {
  label: string;
  tooltip: string;
  tone: InterpretTone;
}) {
  return (
    <span
      title={tooltip}
      style={TONE_STYLE[tone]}
      className="ml-1.5 inline-flex max-w-[9.5rem] cursor-help items-center justify-end rounded-lg px-2 py-0.5 text-right text-[10px] font-semibold leading-tight tracking-tight"
    >
      {label}
    </span>
  );
}
