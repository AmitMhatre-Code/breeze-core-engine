"use client";

import { useEffect, useState } from "react";
import { useOrderConfirm } from "@/components/order/OrderConfirmProvider";
import { LegAggressivePriceInput } from "@/components/strategy-builder/LegAggressivePriceInput";
import { Modal } from "@/components/ui/Modal";
import { ltpAsOrderPrice, positionRowToExecutionLeg } from "@/lib/order-confirm";
import type { PortfolioPositionRecord } from "@/lib/portfolio";
import { sb } from "@/lib/strategy-builder/ui";

type LegState = {
  included: boolean;
  premiumPerUnit: number | undefined;
  aggressiveLimit: boolean;
};

/** "03-Jul-2026" (broker expiry_date format) -> "03JUL26" (compact symbol format). */
function formatExpiryCompact(raw: unknown): string {
  const s = String(raw ?? "").trim();
  const m = s.match(/^(\d{1,2})-([A-Za-z]{3})-(\d{4})$/);
  if (!m) return s.replace(/-/g, "").toUpperCase();
  const [, dd, mon, yyyy] = m;
  return `${dd.padStart(2, "0")}${mon.toUpperCase()}${yyyy.slice(-2)}`;
}

function legLabel(row: PortfolioPositionRecord): string {
  const stock = String(row.stock_code ?? "").trim();
  const expiry = formatExpiryCompact(row.expiry_date);
  const strike = row.strike_price;
  const right = String(row.right ?? "").trim().toLowerCase();
  const abbr = right.startsWith("p") ? "PE" : right.startsWith("c") ? "CE" : "";
  if (!stock || !expiry || strike == null || strike === "") {
    return String(row.option ?? "—");
  }
  return `${stock} ${expiry} ${strike} ${abbr}`.trim();
}

function legQtyLabel(row: PortfolioPositionRecord): string {
  const n = Number(row.quantity);
  return Number.isFinite(n) ? n.toLocaleString("en-IN") : "—";
}

/**
 * Lists every leg in `rows` with an editable price (+ aggressive-limit bolt) and,
 * when there's more than one leg, a checkbox to exclude it. Submitting hands the
 * included legs to the existing multi-leg `OrderExecutionConfirmDialog` via
 * `openExecutionConfirm` (which already runs the license/read-only guard) rather
 * than placing orders itself.
 */
export function SquareOffLegsModal({
  rows,
  open,
  onClose,
}: {
  rows: PortfolioPositionRecord[];
  open: boolean;
  onClose: () => void;
}) {
  const { openExecutionConfirm } = useOrderConfirm();
  const [legStates, setLegStates] = useState<LegState[]>([]);

  useEffect(() => {
    if (!open) return;
    setLegStates(
      rows.map((row) => {
        const n = Number(ltpAsOrderPrice(row.ltp));
        return {
          included: true,
          premiumPerUnit: n > 0 ? n : undefined,
          aggressiveLimit: false,
        };
      }),
    );
    // Snapshot once when the modal opens — deliberately not reactive to `rows`
    // changing underneath (e.g. from portfolio polling) while it's open.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const showCheckbox = rows.length > 1;
  const includedCount = legStates.filter((l) => l.included).length;

  const handleSubmit = () => {
    const first = rows[0];
    if (!first) return;
    const legs = rows
      .map((row, idx) => {
        const state = legStates[idx];
        if (!state?.included) return null;
        return positionRowToExecutionLeg(row, {
          premiumPerUnit: state.premiumPerUnit ?? 0,
          aggressiveLimit: state.aggressiveLimit,
        });
      })
      .filter((l): l is NonNullable<typeof l> => l != null);
    if (!legs.length) return;
    openExecutionConfirm({
      stockCode: String(first.stock_code ?? ""),
      exchangeCode: String(first.exchange_code ?? "NFO"),
      expiryDisplay: String(first.expiry_date ?? ""),
      legs,
      productType: "Options",
    });
    onClose();
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      titleId="square-off-legs-title"
      zIndexClass="z-[110]"
      panelClassName={`${sb.modalPanel} !w-max max-w-[min(96vw,40rem)] mx-auto`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <h3
            id="square-off-legs-title"
            className="text-base font-semibold text-foreground"
          >
            Square off
          </h3>
          <p className="mt-1 text-sm leading-relaxed text-muted">
            Edit the price per leg, or use the bolt for an aggressive limit,
            then confirm.
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

      <ul className="mt-3 max-h-80 divide-y divide-border-soft overflow-y-auto rounded-md border border-border">
        {rows.map((row, idx) => {
          const state = legStates[idx];
          if (!state) return null;
          const label = legLabel(row);
          return (
            <li
              key={`${label}-${idx}`}
              className="flex items-center gap-3 px-3 py-2.5"
            >
              {showCheckbox ? (
                <input
                  type="checkbox"
                  className={sb.checkbox}
                  checked={state.included}
                  onChange={(e) =>
                    setLegStates((prev) =>
                      prev.map((s, i) =>
                        i === idx ? { ...s, included: e.target.checked } : s,
                      ),
                    )
                  }
                  aria-label={`Include ${label}`}
                />
              ) : null}
              <div className="min-w-0 flex-1 text-sm font-medium text-foreground">
                {label}
                <span className="ml-2 text-xs font-normal text-muted">
                  Qty {legQtyLabel(row)}
                </span>
              </div>
              <LegAggressivePriceInput
                aggressive={state.aggressiveLimit}
                premiumPerUnit={state.premiumPerUnit}
                ariaLabel={`Price for ${label}`}
                onAggressiveChange={(checked) =>
                  setLegStates((prev) =>
                    prev.map((s, i) =>
                      i === idx
                        ? {
                            ...s,
                            aggressiveLimit: checked,
                            ...(checked ? { premiumPerUnit: undefined } : {}),
                          }
                        : s,
                    ),
                  )
                }
                onPriceChange={(premiumPerUnit) =>
                  setLegStates((prev) =>
                    prev.map((s, i) =>
                      i === idx ? { ...s, premiumPerUnit } : s,
                    ),
                  )
                }
              />
            </li>
          );
        })}
      </ul>

      <div className="grid grid-cols-1 gap-2 pt-3 sm:grid-cols-2 sm:gap-3">
        <button type="button" className={sb.btnSecondary} onClick={onClose}>
          Cancel
        </button>
        <button
          type="button"
          className={sb.btnPrimary}
          disabled={includedCount === 0}
          onClick={handleSubmit}
        >
          Square Off
        </button>
      </div>
    </Modal>
  );
}
