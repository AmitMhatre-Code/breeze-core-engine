import type { CSSProperties } from "react";
import type { InterpretTone } from "@/lib/dashboard-interpretation";

/** Inline colours so chips stay filled in high-contrast / forced-colour modes and any CSS order bugs. */
const TONE_STYLE: Record<InterpretTone, CSSProperties> = {
  positive: {
    backgroundColor: "var(--up-tint)",
    color: "var(--up)",
    forcedColorAdjust: "none",
  },
  muted: {
    backgroundColor: "var(--accent-tint)",
    color: "var(--accent)",
    forcedColorAdjust: "none",
  },
  caution: {
    backgroundColor: "var(--amber-tint)",
    color: "var(--amber)",
    forcedColorAdjust: "none",
  },
  alarm: {
    backgroundColor: "var(--down-tint)",
    color: "var(--down)",
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
      className="inline-flex max-w-[9.5rem] cursor-help items-center rounded-lg px-2 py-0.5 text-[10px] uppercase tracking-wide"
    >
      {label}
    </span>
  );
}
