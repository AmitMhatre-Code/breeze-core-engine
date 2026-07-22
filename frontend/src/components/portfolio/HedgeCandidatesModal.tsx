"use client";

import { PortfolioHedgePanel } from "@/components/portfolio/PortfolioHedgePanel";
import { Modal } from "@/components/ui/Modal";
import type { PortfolioPositionGroup } from "@/lib/portfolio/groupPositions";
import type { StrategyHedgeCandidate } from "@/lib/hedge/api";
import { sb } from "@/lib/strategy-builder/ui";

type HedgeCandidatesModalProps = {
  group: PortfolioPositionGroup | null;
  open: boolean;
  onClose: () => void;
  selectedCandidate: StrategyHedgeCandidate | null;
  onSelectCandidate: (c: StrategyHedgeCandidate | null) => void;
  onExecute: () => void;
  onLotSizeChange?: (lotSize: number) => void;
};

/** Candidate picker for a group's protective hedge leg — mirrors the Square Off / Exit Rule modal pattern. */
export function HedgeCandidatesModal({
  group,
  open,
  onClose,
  selectedCandidate,
  onSelectCandidate,
  onExecute,
  onLotSizeChange,
}: HedgeCandidatesModalProps) {
  return (
    <Modal
      open={open && group != null}
      onClose={onClose}
      titleId="hedge-candidates-title"
      zIndexClass="z-[110]"
      panelClassName={`${sb.modalPanel} !w-max max-w-[min(96vw,32rem)] mx-auto`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <h3
            id="hedge-candidates-title"
            className="text-base font-semibold text-foreground"
          >
            Hedge
          </h3>
          <p className="mt-1 text-sm leading-relaxed text-muted">
            {group ? `${group.stockCode} · ${group.expiryDate}` : ""} —
            pick a protective leg to preview on the payoff chart, then execute.
          </p>
        </div>
        <button
          type="button"
          className="-m-1 size-9 shrink-0 rounded-lg text-xl leading-none text-muted transition hover:bg-border-soft"
          onClick={onClose}
          aria-label="Close"
        >
          ×
        </button>
      </div>

      {group ? (
        <PortfolioHedgePanel
          group={group}
          selectedCandidate={selectedCandidate}
          onSelectCandidate={onSelectCandidate}
          onExecute={() => {
            onExecute();
            onClose();
          }}
          onLotSizeChange={onLotSizeChange}
          showHeading={false}
        />
      ) : null}
    </Modal>
  );
}
