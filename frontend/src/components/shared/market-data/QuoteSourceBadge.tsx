"use client";

import { InfoPopover } from "@/components/ui/InfoPopover";
import {
  formatQuoteAsOf,
  formatQuoteSourceDetail,
  formatQuoteSourceLabel,
  isBhavcopyStale,
  isLiveQuoteSource,
} from "@/lib/quote-source";
import { useChainBuildPhase, useRelativeTime } from "@/lib/use-relative-time";
import type { QuoteMeta } from "@/lib/strategy-builder/types";

type QuoteSourceBadgeProps = {
  meta: QuoteMeta | null | undefined;
  variant?: "compact" | "default" | "footnote";
  showAsOf?: boolean;
  className?: string;
  /**
   * Identifies the current selection (e.g. `${exchange}:${stock}:${expiry}`) so the
   * "still building" clock resets when the user picks something else. Omit and the
   * badge just shows the static stale state — no animated building phase.
   */
  buildKey?: string;
  /** Shown as a small refresh affordance once the badge gives up animating. */
  onRefresh?: () => void;
  refreshing?: boolean;
};

function variantClasses(
  variant: QuoteSourceBadgeProps["variant"],
  live: boolean,
  stale: boolean,
): string {
  const base =
    "inline-flex max-w-full items-center gap-1.5 rounded-full border font-medium leading-tight";
  if (stale) {
    if (variant === "footnote") {
      return `${base} border-transparent bg-transparent px-0 py-0 text-heading text-down`;
    }
    if (variant === "compact") {
      return `${base} border-down/40 bg-down-tint px-2 py-0.5 text-body text-down-on-tint`;
    }
    return `${base} border-down/40 bg-down-tint px-2.5 py-1 text-micro text-down-on-tint`;
  }
  if (variant === "compact") {
    return `${base} border-border bg-panel2 px-2 py-0.5 text-body text-muted`;
  }
  if (variant === "footnote") {
    return `${base} border-transparent bg-transparent px-0 py-0 text-heading text-muted`;
  }
  if (live) {
    return `${base} border-up/35 bg-up-tint px-2.5 py-1 text-micro text-up-on-tint`;
  }
  return `${base} border-border bg-panel2 px-2.5 py-1 text-micro text-muted`;
}

function PulseDot({ colorClass, pulse }: { colorClass: string; pulse: boolean }) {
  return (
    <span className="relative flex h-1.5 w-1.5 shrink-0" aria-hidden>
      <span
        className={`absolute inline-flex h-full w-full rounded-full ${colorClass} opacity-75 ${
          pulse ? "animate-ping" : ""
        }`}
      />
      <span className={`relative inline-flex h-1.5 w-1.5 rounded-full ${colorClass}`} />
    </span>
  );
}

function LiveDot({ pulse }: { pulse: boolean }) {
  return <PulseDot colorClass="bg-up" pulse={pulse} />;
}

function BuildProgressBar() {
  return (
    <span
      className="pointer-events-none absolute inset-x-2 bottom-1 h-0.5 overflow-hidden rounded-full bg-amber-accent/20"
      aria-hidden
    >
      <span className="qsb-progress-bar absolute inset-y-0 left-0 w-2/5 rounded-full bg-amber-accent" />
    </span>
  );
}

function RefreshAffordance({
  onRefresh,
  refreshing,
}: {
  onRefresh: () => void;
  refreshing?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onRefresh}
      disabled={refreshing}
      title="Refresh"
      aria-label="Refresh option chain"
      className="inline-flex shrink-0 items-center justify-center rounded-full p-1 text-down-on-tint transition hover:bg-down-tint disabled:pointer-events-none disabled:opacity-50"
    >
      <svg
        width="11"
        height="11"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.4"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden
        className={refreshing ? "animate-spin" : ""}
      >
        <path d="M21 12a9 9 0 1 1-3-6.7L21 8" />
        <path d="M21 3v5h-5" />
      </svg>
    </button>
  );
}

function buildingPillClasses(variant: QuoteSourceBadgeProps["variant"]): string {
  const base =
    "qsb-anim relative inline-flex max-w-full items-center gap-1.5 overflow-hidden rounded-full border border-amber-accent/45 bg-amber-tint font-medium leading-tight text-amber-on-tint";
  if (variant === "compact") return `${base} px-2 pt-0.5 pb-1.5 text-body`;
  if (variant === "footnote") return `${base} border-transparent bg-transparent px-0 pb-1.5 pt-0 text-heading`;
  return `${base} px-2.5 pt-1 pb-2 text-micro`;
}

function BuildingBadge({
  meta,
  variant,
  className,
}: {
  meta: QuoteMeta;
  variant: QuoteSourceBadgeProps["variant"];
  className: string;
}) {
  const label = formatQuoteSourceLabel(meta);
  const detail = `${formatQuoteSourceDetail(meta)} A live chain is being assembled from the WebSocket feed in the background — this badge switches over automatically once it's ready.`;
  const pillClassName =
    variant === "footnote"
      ? buildingPillClasses(variant)
      : `${buildingPillClasses(variant)} ${className}`.trim();

  const pill = (
    <span className={pillClassName}>
      <PulseDot colorClass="bg-amber-accent" pulse />
      <span className="truncate">{label}</span>
      <span className="truncate font-normal opacity-80">· building live chain…</span>
      <BuildProgressBar />
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
    <span className="inline-flex items-center gap-1">
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

export function QuoteSourceBadge({
  meta,
  variant = "default",
  showAsOf = true,
  className = "",
  buildKey,
  onRefresh,
  refreshing = false,
}: QuoteSourceBadgeProps) {
  const nowMs = useRelativeTime(meta);
  const phase = useChainBuildPhase(meta, buildKey ?? "");
  if (!meta) return null;

  if (phase === "building") {
    return <BuildingBadge meta={meta} variant={variant} className={className} />;
  }

  const live = isLiveQuoteSource(meta);
  const stale = isBhavcopyStale(meta);
  const label = formatQuoteSourceLabel(meta);
  const asOf =
    showAsOf && variant !== "footnote"
      ? formatQuoteAsOf(meta, nowMs)
      : variant === "footnote"
        ? formatQuoteAsOf(meta, nowMs)
        : null;
  const detail =
    phase === "gave_up" && onRefresh
      ? `${formatQuoteSourceDetail(meta)} Refresh to try building the live chain again.`
      : formatQuoteSourceDetail(meta);

  const pill = (
    <span className={`${variantClasses(variant, live, stale)} ${className}`.trim()}>
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
      {phase === "gave_up" && onRefresh ? (
        <RefreshAffordance onRefresh={onRefresh} refreshing={refreshing} />
      ) : null}
    </span>
  );
}
