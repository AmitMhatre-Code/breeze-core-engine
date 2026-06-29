"use client";

import { InfoPopover } from "@/components/strategy-builder/InfoPopover";
import {
  formatQuoteAsOf,
  formatQuoteSourceDetail,
  formatQuoteSourceLabel,
  isLiveQuoteSource,
} from "@/lib/quote-source";
import { useRelativeTime } from "@/lib/use-relative-time";
import type { QuoteMeta } from "@/lib/strategy-builder/types";

type QuoteSourceBadgeProps = {
  meta: QuoteMeta | null | undefined;
  variant?: "compact" | "default" | "footnote";
  showAsOf?: boolean;
  className?: string;
};

function variantClasses(variant: QuoteSourceBadgeProps["variant"], live: boolean): string {
  const base =
    "inline-flex max-w-full items-center gap-1.5 rounded-full border font-medium leading-tight";
  if (variant === "compact") {
    return `${base} border-zinc-300/80 bg-zinc-100/90 px-2 py-0.5 text-[10px] text-zinc-700 dark:border-zinc-600/70 dark:bg-zinc-800/80 dark:text-zinc-200`;
  }
  if (variant === "footnote") {
    return `${base} border-transparent bg-transparent px-0 py-0 text-[11px] text-zinc-500 dark:text-zinc-400`;
  }
  if (live) {
    return `${base} border-emerald-500/35 bg-emerald-500/10 px-2.5 py-1 text-xs text-emerald-900 dark:border-emerald-500/30 dark:bg-emerald-950/40 dark:text-emerald-100`;
  }
  return `${base} border-zinc-300/80 bg-zinc-50 px-2.5 py-1 text-xs text-zinc-700 dark:border-zinc-600/70 dark:bg-zinc-900/50 dark:text-zinc-200`;
}

function LiveDot({ pulse }: { pulse: boolean }) {
  return (
    <span className="relative flex h-1.5 w-1.5 shrink-0" aria-hidden>
      <span
        className={`absolute inline-flex h-full w-full rounded-full bg-emerald-500 opacity-75 ${
          pulse ? "animate-ping" : ""
        }`}
      />
      <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-emerald-600 dark:bg-emerald-400" />
    </span>
  );
}

export function QuoteSourceBadge({
  meta,
  variant = "default",
  showAsOf = true,
  className = "",
}: QuoteSourceBadgeProps) {
  const nowMs = useRelativeTime(meta);
  if (!meta) return null;

  const live = isLiveQuoteSource(meta);
  const label = formatQuoteSourceLabel(meta);
  const asOf =
    showAsOf && variant !== "footnote"
      ? formatQuoteAsOf(meta, nowMs)
      : variant === "footnote"
        ? formatQuoteAsOf(meta, nowMs)
        : null;
  const detail = formatQuoteSourceDetail(meta);

  const pill = (
    <span className={`${variantClasses(variant, live)} ${className}`.trim()}>
      {live ? <LiveDot pulse={variant !== "footnote"} /> : null}
      <span className="truncate">{label}</span>
      {asOf ? (
        <span className="truncate font-normal opacity-80 tabular-nums">· {asOf}</span>
      ) : null}
    </span>
  );

  if (variant === "footnote") {
    return (
      <span className={`inline-flex flex-wrap items-center gap-1.5 ${className}`.trim()}>
        <span className="text-[11px] text-zinc-500 dark:text-zinc-400">Leg prices from</span>
        {pill}
        <InfoPopover
          title="Quote source"
          ariaLabel="Quote source details"
          learnMoreTopicId="quote-sources"
        >
          <p className="text-sm leading-relaxed text-zinc-700 dark:text-zinc-200">{detail}</p>
        </InfoPopover>
      </span>
    );
  }

  return (
    <span className={`inline-flex items-center gap-1 ${className}`.trim()}>
      {pill}
      <InfoPopover
        title="Quote source"
        ariaLabel="Quote source details"
        learnMoreTopicId="quote-sources"
      >
        <p className="text-sm leading-relaxed text-zinc-700 dark:text-zinc-200">{detail}</p>
      </InfoPopover>
    </span>
  );
}
