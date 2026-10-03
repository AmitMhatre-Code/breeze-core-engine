"use client";

import { useEffect, useState } from "react";
import { Checkbox } from "@/components/ui/Checkbox";
import { Modal } from "@/components/ui/Modal";
import {
  TICKET_TEMPLATES,
  executionDone,
  formatDelta,
  formatInr,
  orderLine,
  previewMatches,
  ticketKey,
  useExecuteTicket,
  useExecution,
  useTicketPreview,
  useTicketTemplate,
  type CondorCampaign,
  type CondorLeg,
  type TicketKind,
  type TicketPreview,
  type TicketRow,
} from "@/lib/condor";

export type TicketRequest = { kind: TicketKind; params?: Record<string, unknown> };

/** The Adjust ticket (docs/dynamic-iron-condor-plan.md 6a): start from a template, edit any
 *  row, preview, execute. The engine's rules come back as warnings to override; the one block
 *  is a short with no long to cap it. Executes one order at a time, wings first. */
export function AdjustTicket({
  open,
  onClose,
  campaign,
  legs,
  request,
}: {
  open: boolean;
  onClose: () => void;
  campaign: CondorCampaign;
  legs: CondorLeg[];
  request: TicketRequest | null;
}) {
  const [kind, setKind] = useState<TicketKind>("blank");
  const [rows, setRows] = useState<TicketRow[]>([]);
  const [note, setNote] = useState("");
  const [templateNote, setTemplateNote] = useState("");
  const [preview, setPreview] = useState<TicketPreview | null>(null);
  const [previewedFor, setPreviewedFor] = useState<string | null>(null);
  const [executionId, setExecutionId] = useState<string | null>(null);
  const template = useTicketTemplate(campaign.id);
  const previewer = useTicketPreview(campaign.id);
  const execute = useExecuteTicket(campaign.id);
  const execution = useExecution(campaign.id, executionId);

  const load = (k: TicketKind, params?: Record<string, unknown>) => {
    setKind(k);
    setPreview(null);
    if (k === "blank") {
      setRows([]);
      setTemplateNote("");
      return;
    }
    template.mutate(
      { kind: k, params },
      {
        onSuccess: (t) => {
          setRows(t.orders.map(({ action, strike, right, quantity, expiry }) => ({ action, strike, right, quantity, expiry })));
          setTemplateNote(t.note);
        },
      },
    );
  };

  // A request from outside (the card's "Execute this suggestion", Portfolio's Square Off)
  // opens the ticket on that template.
  const requestKey = request ? JSON.stringify(request) : null;
  const [loadedFor, setLoadedFor] = useState<string | null>(null);
  useEffect(() => {
    if (open && request && requestKey !== loadedFor) {
      setLoadedFor(requestKey);
      setExecutionId(null);
      load(request.kind, request.params);
    }
    if (!open && loadedFor) setLoadedFor(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, requestKey]);

  const fresh = previewMatches(preview, rows, previewedFor);
  const blocked = Boolean(fresh && preview?.blocked.length);
  const exec = execution.data ?? null;
  const running = Boolean(executionId) && !executionDone(exec);

  const setRow = (i: number, patch: Partial<TicketRow>) =>
    setRows((rs) => rs.map((r, j) => (j === i ? { ...r, ...patch } : r)));

  return (
    <Modal open={open} onClose={running ? () => undefined : onClose} titleId="adjust-ticket-title" zIndexClass="z-[110]"
      panelClassName="app-card mx-auto !w-[min(96vw,56rem)] !max-w-none max-h-[90vh] overflow-y-auto p-5">
      <div className="space-y-4">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 id="adjust-ticket-title" className="app-text-heading">
              Adjust · NIFTY {campaign.cycle?.expiry}
            </h2>
            <p className="text-xs text-muted">
              Orders go out one at a time: new wings, buy-backs, new shorts, then old wings. A failed step stops the rest.
            </p>
          </div>
          <button type="button" className="app-btn-secondary" onClick={onClose} disabled={running}>
            Close
          </button>
        </div>

        {executionId ? (
          <ExecutionView exec={exec} />
        ) : (
          <>
            <TemplatePicker kind={kind} legs={legs} onPick={load} pending={template.isPending} />
            {template.isError ? <p className="text-sm text-down">{(template.error as Error).message}</p> : null}
            {templateNote ? <p className="text-xs text-muted">{templateNote}</p> : null}

            <RowsEditor rows={rows} setRow={setRow} setRows={setRows} cycleExpiry={campaign.cycle?.expiry ?? ""} />

            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                className="app-btn-secondary"
                disabled={!rows.length || previewer.isPending}
                onClick={() =>
                  previewer.mutate(
                    { kind, orders: rows },
                    { onSuccess: (p) => { setPreview(p); setPreviewedFor(ticketKey(rows)); } },
                  )
                }
              >
                {previewer.isPending ? "Pricing…" : fresh ? "Re-price" : "Preview"}
              </button>
              {previewer.isError ? <span className="text-sm text-down">{(previewer.error as Error).message}</span> : null}
            </div>

            {fresh && preview ? <PreviewView p={preview} /> : null}

            {fresh && preview ? (
              <div className="space-y-2">
                <label className="flex flex-col gap-1 text-xs">
                  Note (kept with the execution)
                  <input className="app-input" value={note} maxLength={500} onChange={(e) => setNote(e.target.value)} />
                </label>
                {execute.isError ? <p className="text-sm text-down">{(execute.error as Error).message}</p> : null}
                <button
                  type="button"
                  className={preview.warnings.length ? "app-btn-danger" : "app-btn-primary"}
                  disabled={blocked || execute.isPending}
                  onClick={() =>
                    execute.mutate(
                      { kind, orders: rows, note: note || undefined },
                      { onSuccess: (e) => setExecutionId(e.id) },
                    )
                  }
                >
                  {blocked ? "Blocked" : preview.warnings.length ? "Execute anyway" : "Execute"}
                </button>
              </div>
            ) : rows.length ? (
              <p className="text-xs text-muted">Preview the ticket to price it and see what it changes.</p>
            ) : null}
          </>
        )}
      </div>
    </Modal>
  );
}

function TemplatePicker({
  kind,
  legs,
  onPick,
  pending,
}: {
  kind: TicketKind;
  legs: CondorLeg[];
  onPick: (k: TicketKind, params?: Record<string, unknown>) => void;
  pending: boolean;
}) {
  const [chosen, setChosen] = useState<string[]>([]);
  const [toStrike, setToStrike] = useState("");
  const [toDelta, setToDelta] = useState("");
  const [wingFollows, setWingFollows] = useState(true);
  const [lots, setLots] = useState("1");
  const legKey = (l: CondorLeg) => `${l.strike}-${l.right}`;
  const chosenLegs = legs.filter((l) => chosen.includes(legKey(l))).map((l) => ({ strike: l.strike, right: l.right }));

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-2">
        {TICKET_TEMPLATES.map((t) => (
          <button
            key={t.kind}
            type="button"
            title={t.hint}
            disabled={pending}
            className={t.kind === kind ? "app-btn-primary" : "app-btn-outline"}
            onClick={() => {
              if (t.kind === "roll_selected") {
                onPick(t.kind, { legs: chosenLegs, to_strike: Number(toStrike) || undefined, to_delta: Number(toDelta) || undefined, wing_follows: wingFollows });
              } else if (t.kind === "close_selected") {
                onPick(t.kind, { legs: chosenLegs });
              } else if (t.kind === "add_tranche" || t.kind === "time_roll" || t.kind === "suggested") {
                onPick(t.kind, { lots: Number(lots) || 1 });
              } else {
                onPick(t.kind);
              }
            }}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-3 text-xs">
        <span className="text-muted">Legs for roll / close selected:</span>
        {legs.map((l) => (
          <label key={legKey(l)} className="flex items-center gap-1">
            <Checkbox
              checked={chosen.includes(legKey(l))}
              onChange={(on) => setChosen((c) => (on ? [...c, legKey(l)] : c.filter((x) => x !== legKey(l))))}
              aria-label={`${l.side} ${l.strike} ${l.right}`}
            />
            <span className="font-mono">
              {l.side === "Sell" ? "−" : "+"}
              {l.quantity} {l.strike} {l.right === "Call" ? "CE" : "PE"}
            </span>
          </label>
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-3 text-xs">
        <label className="flex items-center gap-1">
          Roll to strike
          <input className="app-input w-24 font-mono" value={toStrike} onChange={(e) => setToStrike(e.target.value)} />
        </label>
        <label className="flex items-center gap-1">
          or |Δ|
          <input className="app-input w-16 font-mono" value={toDelta} placeholder="0.20" onChange={(e) => setToDelta(e.target.value)} />
        </label>
        <label className="flex items-center gap-1">
          <Checkbox checked={wingFollows} onChange={setWingFollows} aria-label="Wing follows at the same width" />
          Wing follows at the same width
        </label>
        <label className="flex items-center gap-1">
          Lots (tranche, time roll)
          <input className="app-input w-16 font-mono" value={lots} onChange={(e) => setLots(e.target.value)} />
        </label>
      </div>
    </div>
  );
}

function RowsEditor({
  rows,
  setRow,
  setRows,
  cycleExpiry,
}: {
  rows: TicketRow[];
  setRow: (i: number, patch: Partial<TicketRow>) => void;
  setRows: (fn: (rs: TicketRow[]) => TicketRow[]) => void;
  cycleExpiry: string;
}) {
  return (
    <div className="space-y-2">
      <table className="w-full text-sm">
        <thead className="text-xs text-muted">
          <tr>
            <th className="text-left font-normal">Action</th>
            <th className="text-left font-normal">Strike</th>
            <th className="text-left font-normal">Right</th>
            <th className="text-left font-normal">Quantity</th>
            <th className="text-left font-normal">Expiry</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              <td>
                <select className="app-input w-24" value={r.action} onChange={(e) => setRow(i, { action: e.target.value as TicketRow["action"] })}>
                  <option>Buy</option>
                  <option>Sell</option>
                </select>
              </td>
              <td>
                <input className="app-input w-28 font-mono" type="number" step={50} value={r.strike} onChange={(e) => setRow(i, { strike: Number(e.target.value) })} />
              </td>
              <td>
                <select className="app-input w-20" value={r.right} onChange={(e) => setRow(i, { right: e.target.value as TicketRow["right"] })}>
                  <option value="Call">CE</option>
                  <option value="Put">PE</option>
                </select>
              </td>
              <td>
                <input className="app-input w-24 font-mono" type="number" value={r.quantity} onChange={(e) => setRow(i, { quantity: Number(e.target.value) })} />
              </td>
              <td>
                <input className="app-input w-32 font-mono" value={r.expiry ?? cycleExpiry} onChange={(e) => setRow(i, { expiry: e.target.value === cycleExpiry ? null : e.target.value })} />
              </td>
              <td>
                <button type="button" className="app-btn-outline" aria-label="Remove row" onClick={() => setRows((rs) => rs.filter((_, j) => j !== i))}>
                  ×
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <button
        type="button"
        className="app-btn-outline"
        onClick={() => setRows((rs) => [...rs, { action: "Buy", strike: rs.at(-1)?.strike ?? 0, right: "Put", quantity: rs.at(-1)?.quantity ?? 65, expiry: null }])}
      >
        Add row
      </button>
    </div>
  );
}

function PreviewView({ p }: { p: TicketPreview }) {
  const a = p.after;
  return (
    <div className="space-y-3 rounded-lg border border-border bg-panel2 p-3 text-sm">
      {p.blocked.map((b, i) => (
        <p key={i} className="font-semibold text-down">Blocked: {b}</p>
      ))}
      <ol className="list-decimal space-y-0.5 pl-5 font-mono text-xs">
        {p.orders.map((o, i) => (
          <li key={i}>
            {orderLine(o)}
            {o.expiry ? ` · ${o.expiry}` : ""}
          </li>
        ))}
      </ol>
      <div className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
        <span>Cash: <strong className={p.cash_inr < 0 ? "text-down" : "text-up"}>{formatInr(p.cash_inr)}</strong></span>
        <span>Per unit: {p.credit_points != null ? `${p.credit_points.toFixed(1)} pts` : "—"}</span>
        <span>Net Δ/lot after: {formatDelta(a.net_delta_per_lot)}</span>
        <span>Worst loss after: {formatInr(a.worst_loss_inr)}</span>
        <span>Break-evens after: {a.lower_breakeven ? Math.round(a.lower_breakeven) : "—"} – {a.upper_breakeven ? Math.round(a.upper_breakeven) : "—"}</span>
        <span>Total credit after: {formatInr(a.ledger_cash_inr)}</span>
        <span>Margin: {formatInr(p.margin_before_inr)} → {formatInr(p.margin_after_inr)}</span>
        <span>Holding after: {a.legs.length} leg(s) · {a.expiry}</span>
      </div>
      {p.warnings.length ? (
        <ul className="list-disc space-y-0.5 pl-5 text-xs text-down">
          {p.warnings.map((w, i) => (
            <li key={i}>{w}</li>
          ))}
        </ul>
      ) : (
        <p className="text-xs text-muted">No rule is broken.</p>
      )}
    </div>
  );
}

const STEP_LABEL: Record<string, string> = {
  pending: "waiting",
  sending: "working…",
  filled: "filled",
  partial: "partly filled",
  failed: "did not fill",
  not_sent: "not sent",
  unaccounted: "outcome unknown",
};

function ExecutionView({ exec }: { exec: import("@/lib/condor").CondorExecution | null }) {
  if (!exec) return <p className="text-sm text-muted">Starting…</p>;
  return (
    <div className="space-y-2 text-sm">
      <p className="font-semibold">
        {exec.status === "running"
          ? "Executing…"
          : exec.status === "completed"
            ? "Done"
            : exec.status === "unaccounted"
              ? "Stopped: an order's outcome is unknown"
              : exec.status === "interrupted"
                ? "Interrupted by a restart"
                : "Stopped"}
      </p>
      <ol className="space-y-0.5 font-mono text-xs">
        {exec.steps.map((s) => (
          <li key={s.seq}>
            {s.seq}. {orderLine({ ...s, price: s.avg_price })} — {STEP_LABEL[s.status] ?? s.status}
            {s.filled && s.status !== "filled" ? ` (${s.filled} filled)` : ""}
            {s.error ? ` · ${s.error}` : ""}
          </li>
        ))}
      </ol>
      {exec.message ? <p className={exec.status === "completed" ? "text-xs text-muted" : "text-xs text-down"}>{exec.message}</p> : null}
    </div>
  );
}
