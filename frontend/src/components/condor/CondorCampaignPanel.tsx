"use client";

import { useEffect, useState } from "react";
import { AdjustTicket, type TicketRequest } from "@/components/condor/AdjustTicket";
import { CondorSettingsForm } from "@/components/condor/CondorSettingsForm";
import { Modal } from "@/components/ui/Modal";
import {
  actionLabel,
  actionTone,
  campaignForGroup,
  checkKindLabel,
  differenceText,
  formatDelta,
  formatInr,
  orderLine,
  useAssignDifference,
  useCloseCampaign,
  useCondorBacktestDefaults,
  useCondorCampaign,
  useCondorCampaigns,
  useCondorEvaluation,
  useCreateCampaign,
  useExecutions,
  useHandOver,
  useHandoverPreview,
  useLeaveOutDifference,
  useUpdateCampaignSettings,
  type CondorCampaign,
  type CondorDecision,
  type CondorDifference,
  type CondorSettings,
} from "@/lib/condor";

/** The Dynamic Iron Condor section of an expanded Portfolio group (plan section 6). NIFTY only
 *  in v1; any other group renders nothing. */
export function CondorCampaignPanel({
  stockCode,
  expiryDate,
  request,
  onRequestHandled,
}: {
  stockCode: string;
  expiryDate: string;
  /** A ticket opened from outside the card -- Portfolio's Square Off on a campaign group. */
  request?: TicketRequest | null;
  onRequestHandled?: () => void;
}) {
  const list = useCondorCampaigns();
  if (stockCode.trim().toUpperCase() !== "NIFTY") return null;
  const campaign = campaignForGroup(list.data?.campaigns, stockCode, expiryDate);
  return (
    <section className="border-t border-border-soft p-4" aria-label="Dynamic Iron Condor">
      <div className="max-w-4xl">
        {campaign ? (
          <CampaignCard campaign={campaign} request={request ?? null} onRequestHandled={onRequestHandled} />
        ) : (
          <AdoptPrompt expiry={expiryDate} />
        )}
      </div>
    </section>
  );
}

function AdoptPrompt({ expiry }: { expiry: string }) {
  const [open, setOpen] = useState(false);
  const defaults = useCondorBacktestDefaults();
  const [settings, setSettings] = useState<CondorSettings | null>(null);
  const create = useCreateCampaign();
  const value = settings ?? defaults.data?.settings ?? null;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="app-text-heading text-sm">Dynamic Iron Condor</h3>
          <p className="max-w-prose text-xs text-muted">
            Manage this group as a campaign: it is checked at the start and end of each day, rolls and exits are
            suggested by the rules, and a ledger keeps every rupee across rolls.
          </p>
        </div>
        {!open ? (
          <button type="button" className="app-btn-secondary" onClick={() => setOpen(true)}>
            Adopt as a campaign…
          </button>
        ) : null}
      </div>
      {open && value ? (
        <div className="space-y-3 rounded-lg border border-border bg-panel p-3">
          <p className="text-xs text-muted">
            The group&rsquo;s open legs become the campaign&rsquo;s opening fills at the broker&rsquo;s average prices
            (charges estimated). A campaign has its own max-loss, so Profit Booking / Stop Loss cannot be armed on it.
          </p>
          <CondorSettingsForm value={value} onChange={setSettings} disabled={create.isPending} />
          {create.isError ? <p className="text-sm text-down">{(create.error as Error).message}</p> : null}
          <div className="flex gap-2">
            <button
              type="button"
              className="app-btn-primary"
              disabled={create.isPending}
              onClick={() => create.mutate({ settings: value, expiry, adopt: true })}
            >
              {create.isPending ? "Adopting…" : `Adopt NIFTY ${expiry}`}
            </button>
            <button type="button" className="app-btn-secondary" onClick={() => setOpen(false)}>
              Cancel
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function Metric({ label, value, tone, title }: { label: string; value: string; tone?: string; title?: string }) {
  return (
    <div className="min-w-0" title={title}>
      <div className="text-[11px] uppercase tracking-wide text-muted">{label}</div>
      <div className={`font-mono text-sm tabular-nums ${tone ?? ""}`}>{value}</div>
    </div>
  );
}

function toneOf(v: number | null | undefined): string {
  if (v == null) return "";
  return v > 0 ? "text-up" : v < 0 ? "text-down" : "";
}

function DecisionBlock({
  decision,
  heading,
  onExecute,
}: {
  decision: CondorDecision;
  heading: string;
  onExecute?: () => void;
}) {
  const tone = actionTone(decision.action);
  const border = tone === "act" ? "border-accent" : tone === "warn" ? "border-down" : "border-border";
  return (
    <div className={`rounded-lg border ${border} bg-panel p-3`}>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <span className="text-[11px] uppercase tracking-wide text-muted">{heading}</span>
        <span className="text-sm font-semibold">{actionLabel(decision.action)}</span>
      </div>
      <p className="mt-1 text-sm">{decision.text}</p>
      {decision.orders.length ? (
        <ol className="mt-2 list-decimal space-y-0.5 pl-5 font-mono text-xs">
          {decision.orders.map((o, i) => (
            <li key={i}>{orderLine(o)}</li>
          ))}
        </ol>
      ) : null}
      {decision.roll_credit_points != null ? (
        <p className="mt-1 text-xs text-muted">Roll adds {decision.roll_credit_points.toFixed(1)} points a unit, after charges.</p>
      ) : null}
      {onExecute && tone !== "quiet" && decision.action !== "unavailable" ? (
        <button type="button" className="app-btn-primary mt-2" onClick={onExecute}>
          Execute this suggestion…
        </button>
      ) : null}
    </div>
  );
}

function DifferenceRow({ campaignId, d }: { campaignId: string; d: CondorDifference }) {
  const [price, setPrice] = useState(d.broker_avg_price ? String(d.broker_avg_price) : "");
  const assign = useAssignDifference(campaignId);
  const leave = useLeaveOutDifference(campaignId);
  const error = (assign.error ?? leave.error) as Error | null;
  return (
    <li className="space-y-1">
      <p>{differenceText(d)}</p>
      <div className="flex flex-wrap items-center gap-2">
        <label className="flex items-center gap-1 text-xs">
          Traded at
          <input
            type="number"
            step={0.05}
            className="app-input w-24 text-right font-mono"
            value={price}
            onChange={(e) => setPrice(e.target.value)}
          />
        </label>
        <button
          type="button"
          className="app-btn-secondary"
          disabled={assign.isPending || !(Number(price) > 0)}
          onClick={() => assign.mutate({ strike: d.strike, right: d.right, price: Number(price) })}
        >
          Assign to campaign
        </button>
        {d.can_leave_out ? (
          <button
            type="button"
            className="app-btn-outline"
            disabled={leave.isPending}
            onClick={() => leave.mutate({ strike: d.strike, right: d.right })}
          >
            Leave out
          </button>
        ) : null}
      </div>
      {error ? <p className="text-xs text-down">{error.message}</p> : null}
    </li>
  );
}

export function CampaignCard({
  campaign,
  request,
  onRequestHandled,
}: {
  campaign: CondorCampaign;
  request: TicketRequest | null;
  onRequestHandled?: () => void;
}) {
  const evaluation = useCondorEvaluation(campaign.id);
  const detail = useCondorCampaign(campaign.id);
  const executions = useExecutions(campaign.id);
  const [ticket, setTicket] = useState<TicketRequest | null>(null);
  const [ticketOpen, setTicketOpen] = useState(false);
  const openTicket = (r: TicketRequest) => {
    setTicket(r);
    setTicketOpen(true);
  };
  useEffect(() => {
    if (request) {
      openTicket(request);
      onRequestHandled?.();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [request]);
  const ev = evaluation.data;
  const m = ev?.decision.metrics ?? {};
  const s = campaign.settings;
  const cycle = campaign.cycle;
  const pending = (ev?.differences ?? []).filter((d) => !d.left_out);
  const leftOut = (ev?.differences ?? []).filter((d) => d.left_out);
  const last = detail.data?.last_scheduled ?? null;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="app-text-heading text-sm">
          {campaign.origin === "bot" ? (
            <span className="me-2 rounded border border-accent px-1.5 py-px align-middle text-[10px] font-semibold uppercase tracking-wide text-accent">
              Managed by the bot
            </span>
          ) : null}
          Dynamic Iron Condor · cycle {cycle?.expiry ?? "—"}
          {m.dte != null ? ` · ${m.dte} DTE` : ""} · tranche {cycle?.tranches_entered ?? 0}/{s.tranches}
        </h3>
        <span className="text-xs text-muted">
          Checks {s.sod_check_ist} and {s.eod_check_ist} IST · exit at {s.exit_dte} DTE
        </span>
      </div>

      {pending.length ? (
        <div className="rounded-lg border border-down bg-panel p-3 text-sm">
          <p className="font-semibold">The broker&rsquo;s position differs from the campaign&rsquo;s ledger</p>
          <p className="text-xs text-muted">
            Nothing is decided until each difference is assigned to the campaign or left out.
          </p>
          <ul className="mt-2 space-y-2">
            {pending.map((d) => (
              <DifferenceRow key={`${d.strike}-${d.right}`} campaignId={campaign.id} d={d} />
            ))}
          </ul>
        </div>
      ) : null}

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Metric
          label="Campaign P&L"
          value={formatInr(m.campaign_pnl_inr)}
          tone={toneOf(m.campaign_pnl_inr)}
          title="Ledger cash plus what closing now would fetch (longs at the bid, shorts at the ask), net of charges."
        />
        <Metric label="Max loss" value={formatInr(m.max_loss_limit_inr)} title="Checked at the two daily checks only." />
        <Metric
          label="Total net credit"
          value={formatInr(ev?.ledger?.cash_inr)}
          tone={toneOf(ev?.ledger?.cash_inr)}
          title="Every rupee in and out over the campaign, net of charges."
        />
        <Metric
          label="Worst loss at wings"
          value={formatInr(m.worst_loss_at_wings_inr)}
          tone={toneOf(m.worst_loss_at_wings_inr)}
          title="The lowest the campaign can finish at expiry with these legs. A dash: it cannot be worked out, e.g. while a short has no wing."
        />
        <Metric
          label="Break-evens"
          value={`${m.lower_breakeven ? Math.round(m.lower_breakeven).toLocaleString("en-IN") : "—"} – ${
            m.upper_breakeven ? Math.round(m.upper_breakeven).toLocaleString("en-IN") : "—"
          }`}
          title="At expiry, including every past roll's cash."
        />
        <Metric
          label="Short |Δ| PE · CE"
          value={`${m.put_short_abs_delta?.toFixed(2) ?? "—"} · ${m.call_short_abs_delta?.toFixed(2) ?? "—"}`}
          title={m.tested_side ? `Tested side: ${m.tested_side === "Call" ? "calls" : "puts"}` : undefined}
        />
        <Metric
          label="Net Δ per lot"
          value={`${formatDelta(m.net_delta_per_lot)} / ±${s.net_delta_band_per_lot.toFixed(2)}`}
        />
        <Metric
          label="Untested decay"
          value={m.untested_decay_pct != null ? `${m.untested_decay_pct.toFixed(0)}% / ${s.leg_rule_decay_pct}%` : "—"}
          title={m.at_cap ? "At the straddle cap: the untested side cannot roll further." : undefined}
        />
      </div>

      {ev ? (
        <DecisionBlock
          decision={ev.decision}
          heading={ev.indicative ? `Evaluated now · indicative (${ev.stand_ins.join(", ")})` : "Evaluated now · live prices"}
          onExecute={campaign.mode === "paper" ? undefined : () => openTicket({ kind: "suggested" })}
        />
      ) : evaluation.isError ? (
        <p className="text-sm text-down">{(evaluation.error as Error).message}</p>
      ) : (
        <p className="text-sm text-muted">Evaluating…</p>
      )}
      {last ? (
        <p className="text-xs text-muted">
          Last scheduled check · {checkKindLabel(last.check_kind)} {last.at}: <strong>{actionLabel(last.action)}</strong>{" "}
          — {last.text}
        </p>
      ) : (
        <p className="text-xs text-muted">No scheduled check has run yet.</p>
      )}
      {campaign.mode === "paper" ? (
        <p className="text-xs text-muted">Simulation campaign: the bot simulates every action at live prices. Nothing is placed.</p>
      ) : (
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" className="app-btn-secondary" onClick={() => openTicket({ kind: "blank" })}>
          Adjust…
        </button>
        {campaign.origin === "manual" ? <HandOverButton campaign={campaign} /> : null}
        <span className="text-xs text-muted">
          Tickets place one order at a time, wings first. Orders placed elsewhere on this group show up above as
          differences to assign.
        </span>
      </div>
      )}
      <AdjustTicket
        open={ticketOpen}
        onClose={() => setTicketOpen(false)}
        campaign={campaign}
        legs={ev?.ledger?.legs ?? []}
        request={ticket}
      />

      {leftOut.length ? (
        <p className="text-xs text-muted">
          Outside the ledger: {leftOut.map((d) => differenceText(d)).join(" ")}
        </p>
      ) : null}

      <details className="rounded-lg border border-border bg-panel p-3">
        <summary className="cursor-pointer text-sm font-medium">Ledger ({detail.data?.fills.length ?? 0} fills)</summary>
        <div className="mt-2 overflow-x-auto">
          <table className="w-full text-xs">
            <thead className="text-muted">
              <tr>
                <th className="text-left font-normal">When</th>
                <th className="text-left font-normal">Leg</th>
                <th className="text-left font-normal">Kind</th>
                <th className="text-right font-normal">Price</th>
                <th className="text-right font-normal">Charges</th>
                <th className="text-right font-normal">Cash</th>
              </tr>
            </thead>
            <tbody className="font-mono">
              {(detail.data?.fills ?? []).map((f) => (
                <tr key={f.id} title={f.note ?? undefined}>
                  <td>{f.filled_at}</td>
                  <td>
                    {f.side} {f.quantity} × {f.strike} {f.right === "Call" ? "CE" : "PE"}
                  </td>
                  <td>{f.kind}</td>
                  <td className="text-right">{f.price.toFixed(2)}</td>
                  <td className="text-right">{f.charges.toFixed(2)}</td>
                  <td className={`text-right ${toneOf(f.cash)}`}>{formatInr(f.cash, 2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>

      <details className="rounded-lg border border-border bg-panel p-3">
        <summary className="cursor-pointer text-sm font-medium">
          Executions ({executions.data?.executions.length ?? 0})
        </summary>
        <ul className="mt-2 space-y-1 text-xs">
          {(executions.data?.executions ?? []).map((e) => (
            <li key={e.id}>
              <span className="font-mono text-muted">{e.created_at}</span> · {e.kind} · <strong>{e.status}</strong> ·{" "}
              {e.steps.filter((x) => x.status === "filled").length}/{e.steps.length} filled
              {e.message && e.status !== "completed" ? ` — ${e.message}` : ""}
              {e.warnings.length ? ` · overrode: ${e.warnings.join(" ")}` : ""}
            </li>
          ))}
          {executions.data && !executions.data.executions.length ? <li className="text-muted">None yet.</li> : null}
        </ul>
      </details>

      <details className="rounded-lg border border-border bg-panel p-3">
        <summary className="cursor-pointer text-sm font-medium">Check history</summary>
        <ul className="mt-2 space-y-1 text-xs">
          {(detail.data?.decisions ?? []).map((d) => (
            <li key={d.id}>
              <span className="font-mono text-muted">{d.at}</span> · {checkKindLabel(d.check_kind)} ·{" "}
              <strong>{actionLabel(d.action)}</strong> — {d.text}
            </li>
          ))}
          {detail.data && !detail.data.decisions.length ? <li className="text-muted">None yet.</li> : null}
        </ul>
      </details>

      <SettingsEditor campaign={campaign} />
    </div>
  );
}

const SETTING_LABEL: Partial<Record<keyof CondorSettings, string>> = {
  expiry_kind: "Expiries",
  entry_dte: "Entry DTE",
  tranche_cutoff_dte: "Tranche cut-off DTE",
  exit_dte: "Exit DTE",
  tranches: "Tranches",
  entry_check: "Enter tranches at",
  sod_check_ist: "Start-of-day check",
  eod_check_ist: "End-of-day check",
  short_delta: "Short Δ",
  wing_width_pct: "Wing width % of spot",
  leg_rule_delta_floor: "Untested side below Δ",
  leg_rule_decay_pct: "…or decayed %",
  net_delta_band_per_lot: "Net Δ band per lot",
  min_roll_credit_points: "Minimum roll credit",
  no_roll_within_days_of_exit: "No rolls within N days of exit",
  max_loss_inr: "Max loss ₹",
  max_loss_pct_of_ceiling: "…or % of ceiling",
  margin_ceiling_inr: "Margin ceiling ₹",
};

const show = (v: unknown) => (v == null || v === "" ? "off" : String(v));

/** Hand this campaign to the Dynamic Iron Condor bot (#68). Nothing is traded: the bot runs it
 *  from its next check, on its own settings, and gives it back if you trade it or switch it off. */
function HandOverButton({ campaign }: { campaign: CondorCampaign }) {
  const [open, setOpen] = useState(false);
  const preview = useHandoverPreview(campaign.id, open);
  const handOver = useHandOver(campaign.id);
  const p = preview.data;
  const close = () => {
    if (handOver.isPending) return;
    handOver.reset();
    setOpen(false);
  };
  return (
    <>
      <button type="button" className="app-btn-outline" onClick={() => setOpen(true)}>
        Hand to the bot…
      </button>
      <Modal
        open={open}
        onClose={close}
        pending={handOver.isPending}
        titleId={`handover-${campaign.id}`}
        zIndexClass="z-[110]"
        panelClassName="w-full max-w-lg rounded-xl border border-border bg-panel p-5 shadow-pop"
      >
        <h2 id={`handover-${campaign.id}`} className="app-text-heading">
          Hand NIFTY {campaign.cycle?.expiry ?? "—"} to the bot
        </h2>
        <p className="mt-1 text-xs leading-relaxed text-muted">
          Nothing is traded now. From its next check the Dynamic Iron Condor bot manages this campaign on its own
          settings{p && p.mode !== "off" && p.mode !== "paper" ? `, in ${p.mode === "auto" ? "Auto" : "Semi-auto"}` : ""}.
          A ticket you execute on it pauses the bot; switching the bot off or changing this campaign&rsquo;s settings
          hands it back to you.
        </p>
        {!p ? (
          <p className="mt-4 text-sm text-muted">{preview.isError ? (preview.error as Error).message : "Checking…"}</p>
        ) : (
          <div className="mt-4 space-y-3 text-sm">
            {p.blockers.length ? (
              <div className="rounded-lg border border-down/30 bg-down-tint p-3">
                <p className="font-semibold">Not yet</p>
                <ul className="mt-1 list-disc space-y-1 ps-4 text-xs">
                  {p.blockers.map((b) => (
                    <li key={b}>{b}</li>
                  ))}
                </ul>
              </div>
            ) : null}
            {p.settings_changes.length ? (
              <div>
                <p className="text-xs font-semibold">It switches to the bot&rsquo;s settings</p>
                <table className="mt-1 w-full text-xs">
                  <thead className="text-muted">
                    <tr>
                      <th className="text-left font-normal">Setting</th>
                      <th className="text-right font-normal">Now</th>
                      <th className="text-right font-normal">The bot&rsquo;s</th>
                    </tr>
                  </thead>
                  <tbody className="font-mono">
                    {p.settings_changes.map((c) => (
                      <tr key={c.field}>
                        <td className="font-sans">{SETTING_LABEL[c.field] ?? c.field}</td>
                        <td className="text-right">{show(c.campaign)}</td>
                        <td className="text-right">{show(c.bot)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="text-xs text-muted">Its settings already match the bot&rsquo;s.</p>
            )}
            <p className="text-xs text-muted">
              Tranche {p.tranches_entered} of {p.tranches_entered + p.tranches_remaining} entered. {p.sizing}
            </p>
            {p.decision ? (
              <DecisionBlock
                decision={p.decision}
                heading={`On the bot's settings, now${p.indicative ? " · indicative" : ""}`}
              />
            ) : null}
            {handOver.error ? (
              <p className="text-xs text-down" role="alert">
                {(handOver.error as Error).message}
              </p>
            ) : null}
          </div>
        )}
        <div className="mt-4 flex justify-end gap-2">
          <button type="button" className="app-btn-secondary" onClick={close} disabled={handOver.isPending}>
            Cancel
          </button>
          <button
            type="button"
            className="app-btn-primary"
            disabled={!p?.allowed || handOver.isPending}
            onClick={() => handOver.mutate(undefined, { onSuccess: () => setOpen(false) })}
          >
            {handOver.isPending ? "Handing over…" : "Hand to the bot"}
          </button>
        </div>
      </Modal>
    </>
  );
}

function SettingsEditor({ campaign }: { campaign: CondorCampaign }) {
  const [draft, setDraft] = useState<CondorSettings>(campaign.settings);
  const [confirmClose, setConfirmClose] = useState(false);
  const save = useUpdateCampaignSettings(campaign.id);
  const close = useCloseCampaign(campaign.id);
  return (
    <details className="rounded-lg border border-border bg-panel p-3">
      <summary className="cursor-pointer text-sm font-medium">Settings</summary>
      <div className="mt-3 space-y-3">
        {campaign.origin === "bot" ? (
          <p className="text-xs text-muted">
            The bot runs this campaign on its own settings. Saving different ones here hands the campaign back to you;
            to change what the bot runs, use the gear on its card.
          </p>
        ) : null}
        <CondorSettingsForm value={draft} onChange={setDraft} disabled={save.isPending} />
        {save.isError ? <p className="text-sm text-down">{(save.error as Error).message}</p> : null}
        <div className="flex flex-wrap gap-2">
          <button type="button" className="app-btn-primary" disabled={save.isPending} onClick={() => save.mutate(draft)}>
            Save settings
          </button>
          {!confirmClose ? (
            <button type="button" className="app-btn-outline" onClick={() => setConfirmClose(true)}>
              Stop managing…
            </button>
          ) : (
            <>
              <span className="self-center text-xs text-muted">
                Ends the campaign and keeps its ledger. Nothing is traded; the legs stay open.
              </span>
              <button type="button" className="app-btn-danger" disabled={close.isPending} onClick={() => close.mutate()}>
                Stop managing
              </button>
              <button type="button" className="app-btn-secondary" onClick={() => setConfirmClose(false)}>
                Keep
              </button>
            </>
          )}
        </div>
      </div>
    </details>
  );
}
