import type { Outlook } from "@/lib/strategy-builder/types";
import { outlookPillClassName } from "@/lib/strategy-builder/templates";

export function OutlookIcon({
  outlook,
  className = "size-4 shrink-0",
}: {
  outlook: Outlook;
  className?: string;
}) {
  const ring = outlookPillClassName(outlook).split(" ").find((c) => c.startsWith("text-")) ?? "";
  return (
    <svg
      viewBox="0 0 16 16"
      className={`${className} ${ring}`}
      aria-hidden
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {outlook === "bullish" ? (
        <>
          <path d="M2 12 L8 4 L14 12" />
          <path d="M8 4 L8 14" opacity="0.35" />
        </>
      ) : outlook === "bearish" ? (
        <>
          <path d="M2 4 L8 12 L14 4" />
          <path d="M8 2 L8 12" opacity="0.35" />
        </>
      ) : outlook === "neutral" ? (
        <>
          <path d="M2 8 L14 8" />
          <path d="M5 5 L5 11" opacity="0.4" />
          <path d="M11 5 L11 11" opacity="0.4" />
        </>
      ) : (
        <>
          <path d="M2 8 L5 4 L8 10 L11 6 L14 12" />
        </>
      )}
    </svg>
  );
}
