"use client";

import { InfoPopover } from "@/components/ui/InfoPopover";
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
    return `${base} border-border bg-panel2 px-2 py-0.5 text-body text-muted`;
  }
  if (variant === "footnote") {
    return `${base} border-transparent bg-transparent px-0 py-0 text-heading text-muted`;
  }
  if (live) {
    return `${base} border-up/35 bg-up-tint px-2.5 py-1 text-micro text-up`;
  }
  return `${base} border-border bg-panel2 px-2.5 py-1 text-micro text-muted`;
}

function LiveDot({ pulse }: { pulse: boolean }) {
  return (
    <span className="relative flex h-1.5 w-1.5 shrink-0" aria-hidden>
      <span
        className={`absolute inline-flex h-full w-full rounded-full bg-up opacity-75 ${
          pulse ? "animate-ping" : ""
        }`}
      />
      <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-up" />
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
        <span className="text-heading text-muted">Leg prices from</span>
        {pill}
        <InfoPopover
          title="Quote source"
          ariaLabel="Quote source details"
          learnMoreTopicId="quote-sources"
        >
          <p className="text-sm leading-relaxed text-muted">{detail}</p>
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
        <p className="text-sm leading-relaxed text-muted">{detail}</p>
      </InfoPopover>
    </span>
  );
}
