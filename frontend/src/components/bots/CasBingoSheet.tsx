"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import { Modal } from "@/components/ui/Modal";
import { formatIndianMoneyCompact } from "@/lib/format-money-in";
import {
  useCasBingoExecute,
  useCasBingoSheet,
  type CasBingoCandidate,
  type CasBingoExecuteResult,
  type CasBingoSheet as Sheet,
  type CasBingoSheetIndex,
  type CasBingoStructure,
} from "@/lib/use-bots";

function inr(n: number | null | undefined) {
  return n === null || n === undefined ? "—" : formatIndianMoneyCompact(n);
}

function level(n: number | null | undefined) {
  return n ? n.toLocaleString("en-IN", { maximumFractionDigits: 2 }) : "—";
}

/** One structure's row: legs in the order they would be placed (buys first), size, premium,
 *  and what it would take to fund — including any buy-backs. */
function CandidateRow({
  candidate,
  indexCode,
  available,
  creditWarning,
  blocked,
  armed,
  pending,
  onArm,
  onExecute,
}: {
  candidate: CasBingoCandidate;
  indexCode: string;
  available: number | null | undefined;
  creditWarning: string;
  blocked: boolean;
  armed: boolean;
  pending: boolean;
  onArm: () => void;
  onExecute: () => void;
}) {
  const plan = candidate.plan;
  const liq = candidate.liquidation;
  const short = plan && available !== null && available !== undefined && plan.margin_required > available;
  const fundable = !short || Boolean(liq?.covered);
  return (
    <tr className="border-t border-border align-top">
      <td className="px-3 py-2.5">
        <div className="text-body font-semibold">{candidate.label}</div>
        {candidate.family === "credit" && (
          <p className="mt-1 max-w-xs text-hint text-down">{creditWarning}</p>
        )}
      </td>
      <td className="px-3 py-2.5 font-mono text-hint">
        {plan ? (
          <ol className="space-y-0.5">
            {plan.legs.map((leg, i) => (
              <li key={`${leg.right}-${leg.strike_price}-${i}`}>
                <span className={leg.action === "Buy" ? "text-up" : "text-down"}>
                  {leg.action === "Buy" ? "B" : "S"}
                </span>{" "}
                {leg.strike_price} {leg.right === "call" ? "CE" : "PE"}{" "}
                <span className="text-faint">
                  @ {leg.action === "Buy" ? leg.ask : leg.bid}
                </span>
              </li>
            ))}
          </ol>
        ) : (
          <span className="text-faint">{candidate.problem?.reason ?? "Not priced."}</span>
        )}
        {plan?.notes.map((note) => (
          <p key={note} className="mt-1 max-w-xs font-sans text-amber-on-tint">
            {note}
          </p>
        ))}
      </td>
      <td className="px-3 py-2.5 text-right font-mono tabular-nums text-hint">
        {plan ? `${plan.lots} × ${plan.lot_size}` : "—"}
      </td>
      <td className={`px-3 py-2.5 text-right font-mono tabular-nums text-hint ${plan && plan.net_premium_inr >= 0 ? "text-up" : ""}`}>
        {plan ? inr(plan.net_premium_inr) : "—"}
      </td>
      <td className="px-3 py-2.5 text-right font-mono tabular-nums text-hint">
        {plan ? inr(plan.margin_required) : "—"}
        {plan && short && (
          <div className="mt-1 max-w-[16rem] text-left font-sans">
            {liq?.covered ? (
              <span className="text-amber-on-tint">
                Buys back{" "}
                {liq.buybacks
                  .map((b) => `${b.quantity} ${b.strike_price} ${b.right === "call" ? "CE" : "PE"} (≤ ${b.cap_price})`)
                  .join(", ")}{" "}
                to free ~{inr(liq.est_release)}.
              </span>
            ) : (
              <span className="text-down">
                Short of margin{liq?.note ? `: ${liq.note}` : "."}
              </span>
            )}
          </div>
        )}
      </td>
      <td className="px-3 py-2.5 text-right">
        {plan &&
          (armed ? (
            <button type="button" className="app-btn-primary" disabled={pending || blocked || !fundable} onClick={onExecute}>
              {pending ? "Placing…" : "Confirm"}
            </button>
          ) : (
            <button
              type="button"
              className="app-btn-outline"
              disabled={pending || blocked || !fundable}
              onClick={onArm}
              aria-label={`Execute ${candidate.label} on ${indexCode}`}
            >
              Execute
            </button>
          ))}
      </td>
    </tr>
  );
}

function IndexSection({
  index,
  sheet,
  readOnly,
  armedKey,
  pendingKey,
  onArm,
  onExecute,
}: {
  index: CasBingoSheetIndex;
  sheet: Sheet;
  readOnly: boolean;
  armedKey: string | null;
  pendingKey: string | null;
  onArm: (key: string) => void;
  onExecute: (indexCode: string, structure: CasBingoStructure) => void;
}) {
  return (
    <section className="mb-6">
      <header className="mb-2 flex flex-wrap items-baseline gap-x-5 gap-y-1">
        <h3 className="text-subtitle font-bold">
          {index.index_label} <span className="font-mono text-hint text-faint">{index.expiry_display}</span>
        </h3>
        <span className="font-mono text-hint text-faint">open {level(index.day_open)}</span>
        <span className="font-mono text-hint text-faint">spot {level(index.spot)}</span>
        <span className="font-mono text-hint text-faint">
          signal {index.signal.state}
          {index.signal.value !== null ? ` (${index.signal.value.toFixed(2)})` : ""} · {index.signal.name}
          {index.signal_blocked ? <span className="text-amber-on-tint"> · {index.signal_blocked}</span> : null}
        </span>
      </header>
      {index.sg_conflict && (
        <p className="mb-2 rounded-lg border border-down/30 bg-down-tint p-3 text-hint">
          A PB/SL rule is armed on this expiry. It would absorb these legs and square them off with
          its own, so nothing can be placed here until it is disarmed.
        </p>
      )}
      <div className="app-table-wrap">
        <table className="w-full text-left">
          <thead className="app-table-head">
            <tr>
              {["Structure", "Legs (in order)", "Lots", "Net premium", "Margin / cost", ""].map((h, i) => (
                <th key={h || i} className={`px-3 py-2 text-micro font-bold uppercase tracking-[0.07em] ${i >= 2 && i <= 4 ? "text-right" : ""}`}>
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {index.candidates.map((candidate) => {
              const key = `${index.index_code}:${candidate.structure}`;
              return (
                <CandidateRow
                  key={key}
                  candidate={candidate}
                  indexCode={index.index_label}
                  available={sheet.available_margin}
                  creditWarning={sheet.warnings.credit}
                  blocked={readOnly || index.sg_conflict}
                  armed={armedKey === key}
                  pending={pendingKey === key}
                  onArm={() => onArm(key)}
                  onExecute={() => onExecute(index.index_code, candidate.structure)}
                />
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export function CasBingoSheet({
  open,
  readOnly,
  onClose,
}: {
  open: boolean;
  readOnly: boolean;
  onClose: () => void;
}) {
  const titleId = useId();
  const plan = useCasBingoSheet();
  const execute = useCasBingoExecute();
  const [sheet, setSheet] = useState<Sheet | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [armedKey, setArmedKey] = useState<string | null>(null);
  const [pendingKey, setPendingKey] = useState<string | null>(null);
  const [result, setResult] = useState<CasBingoExecuteResult | null>(null);
  const started = useRef(false);

  const load = useCallback(async () => {
    setError(null);
    setArmedKey(null);
    try {
      setSheet(await plan.mutateAsync());
    } catch (e) {
      setSheet(null);
      setError((e as Error)?.message ?? "Could not price the structures.");
    }
    // `plan` is a new object each render; depending on it would re-run on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Opening the sheet IS pricing it, as with the other bots' manual runs.
  useEffect(() => {
    if (!open) {
      started.current = false;
      return;
    }
    if (started.current) return;
    started.current = true;
    setResult(null);
    void load();
  }, [open, load]);

  async function run(indexCode: string, structure: CasBingoStructure) {
    const key = `${indexCode}:${structure}`;
    setError(null);
    setPendingKey(key);
    try {
      const outcome = await execute.mutateAsync({ indexCode, structure });
      setResult(outcome);
      // Prices moved and positions changed: show the sheet as it now stands.
      void load();
    } catch (e) {
      setError((e as Error)?.message ?? "Could not place the orders.");
    } finally {
      setPendingKey(null);
      setArmedKey(null);
    }
  }

  return (
    <Modal open={open} onClose={onClose} variant="fullscreen" titleId={titleId} pending={execute.isPending}>
      <header className="flex flex-wrap items-center gap-3 border-b border-border bg-panel px-4 py-3">
        <h2 id={titleId} className="text-heading font-bold">
          CAS Bingo
        </h2>
        <span className="font-mono text-hint text-faint">
          manual run{sheet?.available_margin !== undefined ? ` · free margin ${inr(sheet.available_margin)}` : ""}
        </span>
        <div className="ms-auto flex items-center gap-2">
          <button type="button" className="app-btn-outline" onClick={() => void load()} disabled={plan.isPending || execute.isPending}>
            {plan.isPending ? "Pricing…" : "Re-price"}
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
        {plan.isPending && !sheet && <p className="app-text-muted text-body">Pricing all five structures…</p>}
        {error && <p className="mb-4 text-body text-down">{error}</p>}
        {result && (
          <div className={`mb-4 rounded-lg border p-3 text-body ${result.opened ? "border-up/30 bg-up-tint" : "border-down/30 bg-down-tint"}`}>
            <p className="font-semibold">{result.opened ? "Placed" : "Not placed"}</p>
            <p className="mt-1 text-hint">{result.reason_text}</p>
          </div>
        )}
        {sheet?.message && <p className="app-text-muted text-body">{sheet.message}</p>}
        {sheet && sheet.liquidation_enabled === false && (
          <p className="mb-3 text-hint text-faint">Liquidation is switched off in settings, so a structure short of margin cannot be funded here.</p>
        )}
        {sheet?.indices.map((index) => (
          <IndexSection
            key={index.index_code}
            index={index}
            sheet={sheet}
            readOnly={readOnly}
            armedKey={armedKey}
            pendingKey={pendingKey}
            onArm={setArmedKey}
            onExecute={(code, structure) => void run(code, structure)}
          />
        ))}
        {sheet && sheet.indices.length > 0 && (
          <p className="mt-2 text-hint text-faint">
            Execute re-prices the structure before placing, buys its long leg first and sells only
            once that has filled. Placed positions are managed by the bot&apos;s stop-loss and target.{" "}
            {sheet.warnings.cas}
          </p>
        )}
      </div>
    </Modal>
  );
}
