"use client";

import { useState } from "react";
import { Checkbox } from "@/components/ui/Checkbox";
import { formatIndianMoneyCompact } from "@/lib/format-money-in";
import {
  BOT_HOLDINGS_WRITER,
  useApproveProposal,
  useProposal,
  useRejectProposal,
  useScan,
  type PlacedLeg,
  type ProposalLeg,
  type SkippedScrip,
} from "@/lib/use-bots";

function money(n: number | null | undefined) {
  return n == null ? "—" : formatIndianMoneyCompact(n);
}

function LegRow({
  leg,
  index,
  checked,
  onToggle,
}: {
  leg: ProposalLeg;
  index: number;
  checked: boolean;
  onToggle: (index: number, checked: boolean) => void;
}) {
  const isPut = leg.right === "put";
  return (
    <tr className="app-table-row align-top">
      <td className="px-3 py-2">
        <Checkbox
          checked={checked}
          onChange={(v) => onToggle(index, v)}
          aria-label={`Include ${leg.stock_code} ${leg.strike_price} ${isPut ? "PE" : "CE"}`}
        />
      </td>
      <td className="px-3 py-2 text-xs">
        <div className="font-medium">
          {leg.stock_code} {leg.strike_price} {isPut ? "PE" : "CE"}
        </div>
        <div className="app-text-muted text-[11px]">{leg.expiry_display}</div>
        {leg.note && (
          <div className="mt-1 text-[11px] text-amber-700 dark:text-amber-400">{leg.note}</div>
        )}
      </td>
      <td className="px-3 py-2 text-right text-xs tabular-nums">
        <div>
          {leg.lots} × {leg.lot_size}
        </div>
        {/* Why the lot count landed where it did — the cap is the whole point of this bot. */}
        <div className="app-text-muted text-[11px]">
          {isPut
            ? "put — capped by cash"
            : `held ${leg.held_quantity ?? "—"}${
                leg.existing_short_lots ? `, ${leg.existing_short_lots} written` : ""
              }`}
        </div>
      </td>
      <td className="px-3 py-2 text-right text-xs tabular-nums">
        <div>{money(leg.premium_total)}</div>
        {leg.premium_basis === "bid" ? (
          <div className="app-text-muted text-[11px]">bid {leg.premium_per_share}</div>
        ) : (
          <div className="text-[11px] text-amber-700 dark:text-amber-400">
            last {leg.premium_per_share} · indicative
          </div>
        )}
      </td>
      <td className="px-3 py-2 text-right text-xs tabular-nums">{money(leg.span_margin)}</td>
      <td className="px-3 py-2 text-right text-xs tabular-nums">{money(leg.elm_margin)}</td>
      <td className="px-3 py-2 text-right text-xs tabular-nums">
        {isPut ? money(leg.delivery_exposure) : "—"}
      </td>
    </tr>
  );
}

function PlacedSummary({ placed }: { placed: PlacedLeg[] }) {
  return (
    <div className="app-card-muted mt-4 p-3">
      <h4 className="text-xs font-medium">Placement result</h4>
      <ul className="mt-2 space-y-1 text-xs">
        {placed.map((p, i) => (
          <li key={`${p.stock_code}-${p.strike_price}-${i}`}>
            <span className="font-medium">
              {p.stock_code} {p.strike_price} {p.right === "put" ? "PE" : "CE"}
            </span>{" "}
            {p.error ? (
              <span className="text-rose-600 dark:text-rose-400">{p.error}</span>
            ) : (
              <span className="text-emerald-700 dark:text-emerald-300">
                {p.quantity} @ {p.limit_price} — order {p.order_ids.join(", ")}
              </span>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

function SkippedList({ skipped }: { skipped: SkippedScrip[] }) {
  if (skipped.length === 0) return null;
  return (
    <details className="mt-4">
      <summary className="app-link cursor-pointer text-xs">
        {skipped.length} holding(s) produced nothing
      </summary>
      <ul className="app-text-muted mt-2 space-y-1 text-[11px]">
        {skipped.map((s, i) => (
          <li key={`${s.stock_code}-${i}`}>
            <span className="font-medium">{s.stock_code}</span> — {s.reason}
          </li>
        ))}
      </ul>
    </details>
  );
}

export function ProposalPanel({ readOnly }: { readOnly: boolean }) {
  const botType = BOT_HOLDINGS_WRITER;
  const { data: proposal, isLoading } = useProposal(botType);
  const scan = useScan();
  const approve = useApproveProposal();
  const reject = useRejectProposal();

  // Selection is local: the scan seeds it (calls on, puts off), and the user reallocates
  // the delivery-cash budget by picking which puts to keep.
  const [overrides, setOverrides] = useState<Record<number, boolean>>({});
  const [error, setError] = useState<string | null>(null);
  const [placed, setPlaced] = useState<PlacedLeg[] | null>(null);

  const legs = proposal?.legs ?? [];
  const isChecked = (i: number) => overrides[i] ?? legs[i]?.selected ?? false;
  const chosen = legs.map((_, i) => i).filter(isChecked);

  const delivery = chosen.reduce(
    (sum, i) => sum + (legs[i].delivery_exposure ?? 0),
    0,
  );
  const premium = chosen.reduce((sum, i) => sum + legs[i].premium_total, 0);
  const span = chosen.reduce((sum, i) => sum + (legs[i].span_margin ?? 0), 0);
  const elm = chosen.reduce((sum, i) => sum + (legs[i].elm_margin ?? 0), 0);
  const budget = proposal?.totals?.delivery_cash_budget ?? 0;
  const overBudget = delivery > budget;
  // Off-market there is no order book, so premiums are priced off the last trade. Those are
  // for planning only — the backend refuses to place them, so don't offer the button.
  const anyIndicative = chosen.some((i) => legs[i].premium_basis !== "bid");

  const skipped = (scan.data?.skipped ?? []) as SkippedScrip[];

  async function runScan() {
    setError(null);
    setPlaced(null);
    setOverrides({});
    try {
      await scan.mutateAsync(botType);
    } catch (e) {
      setError((e as Error)?.message ?? "Scan failed.");
    }
  }

  async function runApprove() {
    setError(null);
    try {
      const result = await approve.mutateAsync({ botType, legIndexes: chosen });
      setPlaced(result.placed);
      setOverrides({});
    } catch (e) {
      setError((e as Error)?.message ?? "Could not place the orders.");
    }
  }

  return (
    <section className="app-card p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="app-text-heading">Proposal</h2>
          <p className="app-text-muted mt-1 max-w-prose text-xs">
            Scan your holdings for writable contracts. Nothing is placed until you approve.
          </p>
        </div>
        <button
          type="button"
          className="app-btn-primary"
          disabled={readOnly || scan.isPending}
          onClick={() => void runScan()}
        >
          {scan.isPending ? "Scanning…" : "Scan holdings"}
        </button>
      </div>

      {error && <p className="mt-3 text-sm text-rose-600 dark:text-rose-400">{error}</p>}
      {placed && <PlacedSummary placed={placed} />}
      <SkippedList skipped={skipped} />

      {isLoading && <p className="app-text-muted mt-4 text-sm">Loading proposal…</p>}

      {!isLoading && !proposal && !placed && (
        <p className="app-text-muted mt-4 text-sm">
          No proposal yet. Run a scan to see what your holdings can write.
        </p>
      )}

      {proposal && legs.length > 0 && (
        <>
          <div className="app-table-wrap mt-4">
            <table className="w-full text-left">
              <thead className="app-table-head">
                <tr>
                  <th className="px-3 py-2 text-xs font-medium" />
                  <th className="px-3 py-2 text-xs font-medium">Contract</th>
                  <th className="px-3 py-2 text-right text-xs font-medium">Size</th>
                  <th className="px-3 py-2 text-right text-xs font-medium">Premium</th>
                  <th className="px-3 py-2 text-right text-xs font-medium">SPAN</th>
                  <th className="px-3 py-2 text-right text-xs font-medium">ELM</th>
                  <th className="px-3 py-2 text-right text-xs font-medium">If assigned</th>
                </tr>
              </thead>
              <tbody>
                {legs.map((leg, i) => (
                  <LegRow
                    key={`${leg.stock_code}-${leg.right}-${leg.strike_price}`}
                    leg={leg}
                    index={i}
                    checked={isChecked(i)}
                    onToggle={(idx, v) => setOverrides((o) => ({ ...o, [idx]: v }))}
                  />
                ))}
              </tbody>
            </table>
          </div>

          <dl className="mt-4 grid gap-3 text-xs sm:grid-cols-4">
            <div>
              <dt className="app-text-muted">Premium</dt>
              <dd className="tabular-nums">{money(premium)}</dd>
            </div>
            <div>
              <dt className="app-text-muted">SPAN + ELM</dt>
              <dd className="tabular-nums">{money(span + elm)}</dd>
            </div>
            <div>
              <dt className="app-text-muted">Delivery exposure</dt>
              <dd className={`tabular-nums ${overBudget ? "text-rose-600 dark:text-rose-400" : ""}`}>
                {money(delivery)}
              </dd>
            </div>
            <div>
              <dt className="app-text-muted">Budget headroom</dt>
              <dd className={`tabular-nums ${overBudget ? "text-rose-600 dark:text-rose-400" : ""}`}>
                {money(budget - delivery)}
              </dd>
            </div>
          </dl>

          {anyIndicative && (
            <p className="mt-2 text-xs text-amber-700 dark:text-amber-400">
              Some premiums are indicative — priced off the last trade because the market is
              closed and there is no live bid. You can plan now, but placing needs an open
              market.
            </p>
          )}

          {overBudget && (
            <p className="mt-2 text-xs text-rose-600 dark:text-rose-400">
              Selected puts would need {money(delivery)} to take delivery, above your{" "}
              {money(budget)} budget. Drop a leg, or raise the budget in settings.
            </p>
          )}

          <div className="mt-4 flex flex-wrap items-center gap-3">
            <button
              type="button"
              className="app-btn-primary"
              disabled={
                readOnly ||
                chosen.length === 0 ||
                overBudget ||
                anyIndicative ||
                approve.isPending
              }
              onClick={() => void runApprove()}
            >
              {approve.isPending
                ? "Placing…"
                : `Place ${chosen.length} order${chosen.length === 1 ? "" : "s"}`}
            </button>
            <button
              type="button"
              className="app-btn-secondary"
              disabled={readOnly || reject.isPending}
              onClick={() => void reject.mutateAsync(botType)}
            >
              Dismiss
            </button>
            {proposal.expires_at && (
              <span className="app-text-muted text-[11px]">
                Prices valid until {proposal.expires_at}
              </span>
            )}
          </div>
        </>
      )}
    </section>
  );
}
