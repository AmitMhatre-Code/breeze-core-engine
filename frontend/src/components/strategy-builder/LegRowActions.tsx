"use client";

import type { StrategyLeg } from "@/lib/strategy-builder/types";

function TrashIcon() {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="M3 6h18" />
      <path d="M8 6V4h8v2" />
      <path d="M19 6l-1 14H6L5 6" />
      <path d="M10 11v6" />
      <path d="M14 11v6" />
    </svg>
  );
}

function CloneIcon() {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <rect x="9" y="9" width="13" height="13" rx="2" />
      <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
    </svg>
  );
}

export function cloneLeg(leg: StrategyLeg): StrategyLeg {
  return {
    ...leg,
    id: `leg-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`,
  };
}

function legDeleteLabel(leg: StrategyLeg): string {
  const right = leg.right === "Call" ? "CE" : "PE";
  return `Delete leg ${leg.strike.toLocaleString("en-IN")} ${right} ${leg.side}`;
}

function legCloneLabel(leg: StrategyLeg): string {
  const right = leg.right === "Call" ? "CE" : "PE";
  return `Clone leg ${leg.strike.toLocaleString("en-IN")} ${right} ${leg.side}`;
}

export function LegRowActions({
  leg,
  onClone,
  onDelete,
}: {
  leg: StrategyLeg;
  onClone: () => void;
  onDelete: () => void;
}) {
  return (
    <div className="flex items-center gap-1">
      <button
        type="button"
        className="rounded p-1 text-muted transition hover:bg-panel2 hover:text-foreground"
        aria-label={legCloneLabel(leg)}
        onClick={onClone}
      >
        <CloneIcon />
      </button>
      <button
        type="button"
        className="rounded p-1 text-down transition hover:bg-down-tint"
        aria-label={legDeleteLabel(leg)}
        onClick={onDelete}
      >
        <TrashIcon />
      </button>
    </div>
  );
}
