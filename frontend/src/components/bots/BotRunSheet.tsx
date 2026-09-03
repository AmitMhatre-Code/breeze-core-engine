"use client";

import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { Checkbox } from "@/components/ui/Checkbox";
import { Modal } from "@/components/ui/Modal";
import { NumberInput } from "@/components/ui/NumberInput";
import { formatIndianMoneyCompact } from "@/lib/format-money-in";
import {
  BOT_HOLDINGS_WRITER,
  BOT_META,
  INDEX_LABEL,
  STRATEGY_LABEL,
  useApproveProposal,
  usePlan,
  useReprice,
  useScan,
  type Bot,
  type LegEdit,
  type PlacedLeg,
  type Proposal,
  type ProposalLeg,
  type SkippedScrip,
} from "@/lib/use-bots";

function money(n: number | null | undefined) {
  return n == null ? "—" : formatIndianMoneyCompact(n);
}

function legLabel(leg: ProposalLeg): string {
  const side = leg.right === "put" ? "PE" : "CE";
  const name = INDEX_LABEL[leg.stock_code] ?? leg.stock_code;
  return `${name} ${leg.strike_price} ${side}`;
}

/** Counts down the priced snapshot's remaining life.
 *
 *  Shown rather than merely enforced: a proposal that silently expires and then refuses to
 *  place looks broken, where a visible countdown is just a deadline. */
function useTimeLeft(expiresAt: string | null | undefined) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!expiresAt) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [expiresAt]);

  if (!expiresAt) return null;
  // Backend timestamps are IST wall-clock without a zone marker.
  const target = new Date(`${expiresAt.replace(" ", "T")}+05:30`).getTime();
  if (Number.isNaN(target)) return null;
  const seconds = Math.max(0, Math.floor((target - now) / 1000));
  const mm = String(Math.floor(seconds / 60)).padStart(2, "0");
  const ss = String(seconds % 60).padStart(2, "0");
  return { seconds, text: `${mm}:${ss}` };
}

const CELL_INPUT =
  "w-16 rounded-t-[3px] border-0 border-b border-muted bg-panel2 px-2 py-1 text-right " +
  "font-mono text-table font-semibold text-text hover:border-accent focus:border-accent-strong " +
  "focus:outline-none disabled:opacity-50 [-moz-appearance:textfield] [appearance:textfield] " +
  "[&::-webkit-inner-spin-button]:m-0 [&::-webkit-inner-spin-button]:appearance-none";

function inr(n: number | null | undefined) {
  return n == null ? "—" : n.toLocaleString("en-IN", { maximumFractionDigits: 2 });
}

/** The leg's current strike as a distance from spot, which is what the editable field
 *  shows and lets the user nudge. `null` when there is no spot to measure against. */
function distancePct(leg: ProposalLeg): number | null {
  if (!leg.spot || leg.spot <= 0) return null;
  return Math.round(Math.abs(leg.strike_price / leg.spot - 1) * 1000) / 10;
}

function LegRow({
  leg,
  index,
  checked,
  edit,
  disabled,
  onToggle,
  onEdit,
  onValidity,
}: {
  leg: ProposalLeg;
  index: number;
  checked: boolean;
  edit: LegEdit | undefined;
  disabled: boolean;
  onToggle: (index: number, checked: boolean) => void;
  onEdit: (index: number, patch: LegEdit) => void;
  onValidity: (key: string, valid: boolean) => void;
}) {
  const isPut = leg.right === "put";
  const shownDistance = edit?.distance_pct ?? distancePct(leg) ?? undefined;
  return (
    <tr className="app-table-row align-top">
      <td className="px-3 py-2">
        <Checkbox
          checked={checked}
          disabled={disabled}
          onChange={(v) => onToggle(index, v)}
          aria-label={`Include ${legLabel(leg)}`}
        />
      </td>
      <td className="px-3 py-2 text-body">
        <div className="font-semibold">
          {INDEX_LABEL[leg.stock_code] ?? leg.stock_code} {isPut ? "PE" : "CE"}
        </div>
        <div className="font-mono text-micro text-faint">{leg.expiry_display}</div>
        {leg.strategy && (
          <div className="text-micro text-accent-on-tint">
            {STRATEGY_LABEL[leg.strategy]}
          </div>
        )}
        {leg.note && <div className="mt-1 text-micro text-amber-on-tint">{leg.note}</div>}
      </td>
      <td className="px-3 py-2 text-right font-mono text-table tabular-nums text-muted">
        {inr(leg.spot)}
      </td>
      <td className="px-3 py-2 text-right">
        {/* The strike itself is not typed any more — it follows the distance below it,
            snapped to a real strike against the current spot, exactly as the bot does. */}
        <span className="font-mono text-table tabular-nums">{leg.strike_price}</span>
      </td>
      <td className="px-3 py-2 text-right">
        {shownDistance === undefined ? (
          <span className="font-mono text-table text-faint">—</span>
        ) : (
          <NumberInput
            value={shownDistance}
            min={0.25}
            max={50}
            step={0.25}
            disabled={disabled}
            aria-label={`${leg.stock_code} distance from spot %`}
            className={CELL_INPUT}
            onValidityChange={(v) => onValidity(`dist-${index}`, v)}
            onChange={(v) => onEdit(index, { distance_pct: v })}
          />
        )}
        <div className="font-mono text-micro text-faint">
          {isPut ? "below" : "above"} spot
        </div>
      </td>
      <td className="px-3 py-2 text-right">
        <NumberInput
          value={edit?.lots ?? leg.lots}
          min={1}
          max={999}
          disabled={disabled}
          aria-label={`${leg.stock_code} lots`}
          className={CELL_INPUT}
          onValidityChange={(v) => onValidity(`lots-${index}`, v)}
          onChange={(v) => onEdit(index, { lots: v })}
        />
        <div className="font-mono text-micro text-faint">× {leg.lot_size}</div>
      </td>
      <td className="px-3 py-2 text-right font-mono text-table tabular-nums">
        {leg.premium_basis === "bid" ? (
          <span>{leg.premium_per_share}</span>
        ) : (
          // Amber is the only in-row cue now that the "ind." suffix is gone (legend below);
          // the title and the off-screen word keep it from being colour-only.
          <span className="text-amber-on-tint" title="Indicative — priced off the last trade">
            {leg.premium_per_share}
            <span className="sr-only"> (indicative)</span>
          </span>
        )}
      </td>
      <td className="px-3 py-2 text-right font-mono text-table tabular-nums text-up">
        {money(leg.premium_total)}
      </td>
      <td className="px-3 py-2 text-right font-mono text-table tabular-nums">
        {money((leg.span_margin ?? 0) + (leg.elm_margin ?? 0))}
      </td>
      <td className="px-3 py-2 text-right font-mono text-table tabular-nums">
        {isPut ? money(leg.delivery_exposure) : "—"}
      </td>
    </tr>
  );
}

export function BotRunSheet({
  bot,
  open,
  readOnly,
  onClose,
}: {
  bot: Bot;
  open: boolean;
  readOnly: boolean;
  onClose: () => void;
}) {
  const meta = BOT_META[bot.bot_type];
  const isHoldings = bot.bot_type === BOT_HOLDINGS_WRITER;
  const titleId = useId();

  const scan = useScan();
  const plan = usePlan();
  const reprice = useReprice();
  const approve = useApproveProposal();

  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [skipped, setSkipped] = useState<SkippedScrip[]>([]);
  const [overrides, setOverrides] = useState<Record<number, boolean>>({});
  const [edits, setEdits] = useState<Record<number, LegEdit>>({});
  const [invalidFields, setInvalidFields] = useState<Record<string, boolean>>({});
  const [placed, setPlaced] = useState<PlacedLeg[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const started = useRef(false);

  const handleValidity = useCallback((key: string, valid: boolean) => {
    setInvalidFields((current) => {
      if (Boolean(current[key]) === !valid) return current;
      return { ...current, [key]: !valid };
    });
  }, []);
  const anyInvalid = Object.values(invalidFields).some(Boolean);

  // Memoised because `?? []` yields a fresh array each render, which would re-create
  // every dependent callback and re-fire the debounced re-price on unrelated renders.
  const legs = useMemo(() => proposal?.legs ?? [], [proposal]);
  const timeLeft = useTimeLeft(proposal?.expires_at);
  const running = scan.isPending || plan.isPending;

  const start = useCallback(async () => {
    setError(null);
    setPlaced(null);
    setOverrides({});
    setEdits({});
    setInvalidFields({});
    try {
      const result = isHoldings
        ? await scan.mutateAsync(bot.bot_type)
        : await plan.mutateAsync(bot.bot_type);
      setProposal(result.proposal);
      setSkipped(result.skipped ?? []);
    } catch (e) {
      setProposal(null);
      setError((e as Error)?.message ?? "The run could not be started.");
    }
    // `scan`/`plan` are new objects each render; depending on them would re-run the effect
    // on every keystroke.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bot.bot_type, isHoldings]);

  // One run per opening. Opening the sheet IS pressing start — a sheet that opened empty
  // with a second button to press would be a step that says nothing.
  useEffect(() => {
    if (!open) {
      started.current = false;
      return;
    }
    if (started.current) return;
    started.current = true;
    void start();
  }, [open, start]);

  const isChecked = useCallback(
    (i: number) => overrides[i] ?? legs[i]?.selected ?? false,
    [overrides, legs],
  );

  function toggle(index: number, checked: boolean) {
    const group = legs[index]?.group_key;
    setOverrides((current) => {
      const next = { ...current, [index]: checked };
      // Half a strangle is a naked short — not the shape the bot proposed or the user
      // picked — so both sides move together.
      if (group) {
        legs.forEach((leg, i) => {
          if (i !== index && leg.group_key === group) next[i] = checked;
        });
      }
      return next;
    });
  }

  const chosen = useMemo(
    () => legs.map((_, i) => i).filter(isChecked),
    [legs, isChecked],
  );

  // Margin is not linear in lot count — it comes from the broker, not multiplication — so
  // an edit goes back to the source rather than being scaled here. Debounced, because a
  // margin call per keystroke would hammer a rate-limited API.
  const editsRef = useRef(edits);
  editsRef.current = edits;
  useEffect(() => {
    if (!open || anyInvalid || Object.keys(edits).length === 0) return;
    const id = setTimeout(() => {
      reprice
        .mutateAsync({ botType: bot.bot_type, legIndexes: chosen, edits: editsRef.current })
        .then((fresh) => setProposal(fresh))
        .catch(() => {
          /* Keep the last good prices; execute re-prices again and fails closed. */
        });
    }, 700);
    return () => clearTimeout(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [edits, open, anyInvalid, bot.bot_type]);

  const totals = useMemo(() => {
    const premium = chosen.reduce((sum, i) => sum + legs[i].premium_total, 0);
    const margin = chosen.reduce(
      (sum, i) => sum + (legs[i].span_margin ?? 0) + (legs[i].elm_margin ?? 0),
      0,
    );
    const delivery = chosen.reduce((sum, i) => sum + (legs[i].delivery_exposure ?? 0), 0);
    return { premium, margin, delivery };
  }, [chosen, legs]);

  const budget = proposal?.totals?.delivery_cash_budget ?? 0;
  const overBudget = isHoldings && totals.delivery > budget;
  // Off-market there is no order book, so premiums come off the last trade. Planning is
  // fine; placing is not, and the backend refuses either way — so don't offer the button.
  const anyIndicative = chosen.some((i) => legs[i].premium_basis !== "bid");
  const expired = timeLeft?.seconds === 0;
  const blocked =
    readOnly ||
    chosen.length === 0 ||
    overBudget ||
    anyIndicative ||
    expired ||
    anyInvalid;

  async function execute() {
    setError(null);
    try {
      const result = await approve.mutateAsync({
        botType: bot.bot_type,
        legIndexes: chosen,
        edits,
      });
      setPlaced(result.placed);
      setProposal(null);
    } catch (e) {
      setError((e as Error)?.message ?? "Could not place the orders.");
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      variant="fullscreen"
      titleId={titleId}
      pending={approve.isPending}
    >
      <header className="flex flex-wrap items-center gap-3 border-b border-border bg-panel px-4 py-3">
        <h2 id={titleId} className="text-heading font-bold">
          {meta.title}
        </h2>
        <span className="font-mono text-hint text-faint">
          manual run
          {proposal?.created_at ? ` · priced ${proposal.created_at.slice(11, 19)}` : ""}
          {timeLeft ? ` · ${expired ? "expired" : `expires in ${timeLeft.text}`}` : ""}
        </span>
        <div className="ms-auto flex items-center gap-2">
          <button
            type="button"
            className="app-btn-outline"
            onClick={() => void start()}
            disabled={running || approve.isPending}
          >
            {running ? "Running…" : "Re-price"}
          </button>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close run"
            className="rounded p-1 text-faint transition hover:text-text focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/45"
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="size-4">
              <path d="M18 6 6 18M6 6l12 12" />
            </svg>
          </button>
        </div>
      </header>

      <div className="flex-1 overflow-auto p-4">
        {running && <p className="app-text-muted text-body">Pricing what you can write…</p>}

        {error && <p className="mb-4 text-body text-down">{error}</p>}

        {placed && (
          <div className="app-card-muted mb-4 p-3">
            <h3 className="text-body font-semibold">Placement result</h3>
            <ul className="mt-2 space-y-1 text-body">
              {placed.map((p, i) => (
                <li key={`${p.stock_code}-${p.strike_price}-${i}`}>
                  <span className="font-semibold">
                    {INDEX_LABEL[p.stock_code] ?? p.stock_code} {p.strike_price}{" "}
                    {p.right === "put" ? "PE" : "CE"}
                  </span>{" "}
                  {p.error ? (
                    <span className="text-down">{p.error}</span>
                  ) : (
                    <span className="text-up">
                      {p.quantity} @ {p.limit_price} — order {p.order_ids.join(", ")}
                    </span>
                  )}
                </li>
              ))}
            </ul>
          </div>
        )}

        {!running && !proposal && !placed && (
          <p className="app-text-muted text-body">
            Nothing to trade right now.
            {skipped.length > 0 && " See the reasons below."}
          </p>
        )}

        {proposal && legs.length > 0 && (
          <div className="app-table-wrap">
            <table className="w-full text-left">
              <thead className="app-table-head">
                <tr>
                  <th className="px-3 py-2" />
                  <th className="px-3 py-2 text-micro font-bold uppercase tracking-[0.07em]">
                    Contract
                  </th>
                  <th className="px-3 py-2 text-right text-micro font-bold uppercase tracking-[0.07em]">
                    Spot
                  </th>
                  <th className="px-3 py-2 text-right text-micro font-bold uppercase tracking-[0.07em]">
                    Strike
                  </th>
                  <th className="px-3 py-2 text-right text-micro font-bold uppercase tracking-[0.07em]">
                    Dist %
                  </th>
                  <th className="px-3 py-2 text-right text-micro font-bold uppercase tracking-[0.07em]">
                    Lots
                  </th>
                  <th className="px-3 py-2 text-right text-micro font-bold uppercase tracking-[0.07em]">
                    Bid
                  </th>
                  <th className="px-3 py-2 text-right text-micro font-bold uppercase tracking-[0.07em]">
                    Premium
                  </th>
                  <th className="px-3 py-2 text-right text-micro font-bold uppercase tracking-[0.07em]">
                    Margin
                  </th>
                  <th className="px-3 py-2 text-right text-micro font-bold uppercase tracking-[0.07em]">
                    If assigned
                  </th>
                </tr>
              </thead>
              <tbody>
                {legs.map((leg, i) => (
                  <LegRow
                    key={`${leg.stock_code}-${leg.right}-${leg.strike_price}-${i}`}
                    leg={leg}
                    index={i}
                    checked={isChecked(i)}
                    edit={edits[i]}
                    disabled={readOnly || approve.isPending}
                    onToggle={toggle}
                    onValidity={handleValidity}
                    onEdit={(index, patch) =>
                      setEdits((current) => ({
                        ...current,
                        [index]: { ...current[index], ...patch },
                      }))
                    }
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}

        {reprice.isPending && (
          <p className="mt-2 font-mono text-hint text-faint">Re-pricing…</p>
        )}

        {anyIndicative && (
          <div className="mt-3 flex items-start gap-2 rounded-lg border border-amber/30 bg-amber-tint p-3 text-hint text-text">
            <span
              aria-hidden
              className="mt-px shrink-0 font-mono text-table tabular-nums text-amber-on-tint"
            >
              0.00
            </span>
            <p>
              Bids in this colour are indicative — priced off the last trade because the
              market is closed and there is no live bid. You can plan now, but placing needs
              an open market.
            </p>
          </div>
        )}

        {overBudget && (
          <p className="mt-3 rounded-lg border border-down/30 bg-down-tint p-3 text-hint text-text">
            The selected puts would need {money(totals.delivery)} to take delivery, above
            your {money(budget)} budget. Drop a leg, or raise the budget in settings.
          </p>
        )}

        {expired && (
          <p className="mt-3 rounded-lg border border-amber/30 bg-amber-tint p-3 text-hint text-text">
            These prices have expired. Re-price before executing.
          </p>
        )}

        {anyInvalid && (
          <p className="mt-3 rounded-lg border border-down/30 bg-down-tint p-3 text-hint text-text">
            A highlighted field is empty or out of range. Enter a value to continue.
          </p>
        )}

        {skipped.length > 0 && (
          <details className="mt-4">
            <summary className="app-link cursor-pointer text-hint">
              {skipped.length} produced nothing
            </summary>
            <ul className="mt-2 space-y-1 text-hint text-faint">
              {skipped.map((s, i) => (
                <li key={`${s.stock_code}-${i}`}>
                  <span className="font-semibold text-muted">{s.stock_code}</span> —{" "}
                  {s.reason}
                </li>
              ))}
            </ul>
          </details>
        )}
      </div>

      <footer className="flex flex-wrap items-center justify-between gap-4 border-t border-border bg-panel px-4 py-3">
        <dl className="flex flex-wrap gap-6">
          <div>
            <dt className="text-micro font-bold uppercase tracking-[0.07em] text-faint">
              Premium
            </dt>
            <dd className="m-0 font-mono text-base font-semibold tabular-nums text-up">
              {money(totals.premium)}
            </dd>
          </div>
          <div>
            <dt className="text-micro font-bold uppercase tracking-[0.07em] text-faint">
              Margin needed
            </dt>
            <dd className="m-0 font-mono text-base font-semibold tabular-nums">
              {money(totals.margin)}
            </dd>
          </div>
          {isHoldings && (
            <div>
              <dt className="text-micro font-bold uppercase tracking-[0.07em] text-faint">
                Delivery headroom
              </dt>
              <dd
                className={`m-0 font-mono text-base font-semibold tabular-nums ${
                  overBudget ? "text-down" : ""
                }`}
              >
                {money(budget - totals.delivery)}
              </dd>
            </div>
          )}
        </dl>
        <div className="flex gap-2">
          <button type="button" className="app-btn-secondary" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="app-btn-primary"
            disabled={blocked || approve.isPending}
            onClick={() => void execute()}
          >
            {approve.isPending
              ? "Placing…"
              : `Execute ${chosen.length} order${chosen.length === 1 ? "" : "s"}`}
          </button>
        </div>
      </footer>
    </Modal>
  );
}
