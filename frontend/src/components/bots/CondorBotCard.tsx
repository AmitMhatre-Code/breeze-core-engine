"use client";

import { useState } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { BotStatusRow } from "@/components/bots/BotStatusRow";
import { PriorityPill } from "@/components/bots/PriorityPill";
import { CondorSettingsForm } from "@/components/condor/CondorSettingsForm";
import { Modal } from "@/components/ui/Modal";
import { apiClient } from "@/lib/api-client";
import { formatInr, useStartCondorBacktest, type CondorCampaign, type CondorSettings } from "@/lib/condor";
import { BOT_META, useUpdateBot, type Bot } from "@/lib/use-bots";

/** The Dynamic Iron Condor bot (docs/dynamic-iron-condor-plan.md section 7). Its modes unlock
 *  in order -- a completed backtest of these exact settings, then a paper cycle, then a ticket
 *  approved on Telegram -- and the server refuses whatever the evidence does not yet allow. */

type CondorBotConfig = {
  mode: "paper" | "telegram" | "auto";
  campaign: CondorSettings;
  exit_action: "time_roll" | "close";
  lots_per_tranche: number | null;
  proposal_ttl_minutes: number;
  paused: boolean;
  paused_reason: string | null;
};

type Overview = {
  eligibility: {
    settings_hash: string;
    backtest: { run_id: string; created_at: string; from: string; to: string; closed_pnl: number | null; open_campaign_cash: number | null; max_drawdown: number | null } | null;
    paper_cycles: number;
    approved_executions: number;
    may_enable: boolean;
    may_telegram: boolean;
    may_auto: boolean;
  };
  campaign: CondorCampaign | null;
};

type Segment = "off" | "paper" | "telegram" | "auto";
const SEGMENTS: Segment[] = ["off", "paper", "telegram", "auto"];
const LABEL: Record<Segment, string> = { off: "Off", paper: "Paper", telegram: "Telegram", auto: "Auto" };
const BLURB: Record<Segment, string> = {
  off: "Decides nothing. A live campaign it ran is handed back to you.",
  paper: "Acts at the two daily checks on paper, at live prices. Places nothing.",
  telegram: "Asks on Telegram before each action; a tap places it, wings first.",
  auto: "Acts at the two daily checks on its own, wings first.",
};

const HEADER_ICON_BTN =
  "grid size-8 shrink-0 place-items-center rounded-lg text-muted transition hover:text-accent focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/45 disabled:pointer-events-none disabled:opacity-40";

function GearIcon() {
  return (
    <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden>
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.7 1.7 0 0 0 .34 1.88l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.7 1.7 0 0 0-1.88-.34 1.7 1.7 0 0 0-1 1.56V21a2 2 0 1 1-4 0v-.09A1.7 1.7 0 0 0 8.9 19.3a1.7 1.7 0 0 0-1.88.34l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.7 1.7 0 0 0 4.6 15a1.7 1.7 0 0 0-1.56-1H3a2 2 0 1 1 0-4h.09A1.7 1.7 0 0 0 4.7 8.9a1.7 1.7 0 0 0-.34-1.88l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.7 1.7 0 0 0 9 4.6a1.7 1.7 0 0 0 1-1.56V3a2 2 0 1 1 4 0v.09a1.7 1.7 0 0 0 1 1.56 1.7 1.7 0 0 0 1.88-.34l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.7 1.7 0 0 0 19.4 9V9a1.7 1.7 0 0 0 1.56 1H21a2 2 0 1 1 0 4h-.09a1.7 1.7 0 0 0-1.51 1Z" />
    </svg>
  );
}

export function CondorBotCard({ bot, readOnly }: { bot: Bot; readOnly: boolean }) {
  const meta = BOT_META[bot.bot_type];
  const config = bot.config as unknown as CondorBotConfig;
  const update = useUpdateBot();
  const backtest = useStartCondorBacktest();
  const [error, setError] = useState<string | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const overview = useQuery({
    queryKey: ["bots", "condor-overview", bot.updated_at],
    queryFn: ({ signal }) => apiClient.get<Overview>("/api/condor/bot", signal),
  });
  const e = overview.data?.eligibility;
  const camp = overview.data?.campaign ?? null;
  const segment: Segment = bot.enabled ? config.mode : "off";

  const locked = (s: Segment): string | null => {
    if (s === "off") return null;
    // Locked until the evidence has loaded: an unknown is never an unlock.
    if (!e) return "Checking what these settings have earned…";
    if (!e.may_enable) return "Needs a completed backtest of these settings.";
    if (s === "telegram" && !e.may_telegram) return "Unlocks after one paper cycle.";
    if (s === "auto" && !e.may_auto) return "Unlocks after a paper cycle and a Telegram-approved ticket on these settings.";
    return null;
  };

  async function choose(s: Segment) {
    if (s === segment) return;
    setError(null);
    try {
      await update.mutateAsync({
        botType: bot.bot_type,
        enabled: s !== "off",
        ...(s === "off" ? {} : { config: { mode: s } }),
      });
    } catch (err) {
      setError((err as Error)?.message ?? "Could not save.");
    }
  }

  async function resume() {
    setError(null);
    try {
      await update.mutateAsync({ botType: bot.bot_type, config: { paused: false, paused_reason: null } });
    } catch (err) {
      setError((err as Error)?.message ?? "Could not resume.");
    }
  }

  const today = new Date();
  const yesterday = new Date(today.getTime() - 86_400_000).toISOString().slice(0, 10);
  const pnl = e?.backtest ? (e.backtest.closed_pnl ?? 0) + (e.backtest.open_campaign_cash ?? 0) : null;

  return (
    <>
      <section className="app-card flex h-full flex-col p-4">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <PriorityPill bot={bot} readOnly={readOnly} onError={setError} />
            <h2 className="app-text-heading mt-1.5">{meta.title}</h2>
            <p className="app-text-muted mt-1 line-clamp-2 min-h-[2lh] text-hint">{meta.blurb}</p>
          </div>
          <button type="button" aria-label={`${meta.title} settings`} onClick={() => setSettingsOpen(true)} className={HEADER_ICON_BTN}>
            <GearIcon />
          </button>
        </div>

        <div className="mt-4 flex flex-col gap-1.5">
          <BotStatusRow
            tone={!bot.enabled ? "idle" : config.paused ? "guarded" : config.mode === "auto" ? "live" : "guarded"}
            label={!bot.enabled ? "Idle" : config.paused ? "Paused" : "Armed"}
            badge={bot.enabled && config.paused ? "Paused" : bot.enabled && config.mode !== "auto" ? (config.mode === "paper" ? "Paper" : "Asks first") : undefined}
          />
          {config.paused ? (
            <div className="flex items-center justify-between gap-2 text-hint">
              <span className="line-clamp-2 text-amber-on-tint">Paused: {config.paused_reason ?? "by you"}</span>
              <button type="button" className="app-btn-secondary" disabled={readOnly || update.isPending} onClick={resume}>
                Resume
              </button>
            </div>
          ) : null}
          <dl className="mt-1 grid gap-1.5 text-hint">
            <div className="flex items-baseline justify-between gap-3">
              <dt className="text-faint">Campaign</dt>
              <dd className="m-0 font-mono">
                {camp ? (
                  <Link className="app-link" href={camp.mode === "paper" ? "/iron-condors" : "/portfolio"}>
                    {camp.mode} · {camp.cycle?.expiry ?? "—"}
                  </Link>
                ) : (
                  "—"
                )}
              </dd>
            </div>
            <div className="flex items-baseline justify-between gap-3">
              <dt className="text-faint">Backtest</dt>
              <dd className="m-0 font-mono">
                {!e ? (
                  "…"
                ) : e.backtest ? (
                  <span title={`${e.backtest.from} → ${e.backtest.to}, max drawdown ${formatInr(e.backtest.max_drawdown)}`}>
                    {formatInr(pnl)} · {e.backtest.from.slice(0, 7)}→{e.backtest.to.slice(0, 7)}
                  </span>
                ) : (
                  <button
                    type="button"
                    className="app-link"
                    disabled={backtest.isPending}
                    onClick={() =>
                      backtest.mutate(
                        {
                          settings: config.campaign,
                          from_date: "2026-01-05",
                          to_date: yesterday,
                          exit_action: config.exit_action,
                          lots_per_tranche: config.lots_per_tranche,
                        },
                        { onError: (err) => setError((err as Error).message) },
                      )
                    }
                  >
                    {backtest.isSuccess ? "Running — see Iron Condors" : "Backtest these settings"}
                  </button>
                )}
              </dd>
            </div>
            <div className="flex items-baseline justify-between gap-3">
              <dt className="text-faint">Paper cycles · approved</dt>
              <dd className="m-0 font-mono">
                {e?.paper_cycles ?? 0} · {e?.approved_executions ?? 0}
              </dd>
            </div>
          </dl>
        </div>

        <div className="mt-auto pt-4">
          <div className="grid grid-cols-4 gap-[3px] rounded-full border border-border bg-panel2 p-[3px]" role="group" aria-label="Iron Condor bot mode">
            {SEGMENTS.map((s) => {
              const active = segment === s;
              const why = locked(s);
              return (
                <button
                  key={s}
                  type="button"
                  aria-pressed={active}
                  title={why ?? BLURB[s]}
                  disabled={update.isPending || Boolean(why) || (readOnly && s !== "off")}
                  onClick={() => choose(s)}
                  className={[
                    "rounded-full px-1.5 py-1.5 text-micro font-bold uppercase tracking-[0.03em] transition",
                    "focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/45",
                    "disabled:pointer-events-none disabled:opacity-50",
                    active
                      ? s === "auto"
                        ? "bg-up-btn text-up-ink"
                        : s === "off"
                          ? "bg-elevated text-text"
                          : "bg-amber-accent text-amber-ink"
                      : "text-faint hover:text-text",
                  ].join(" ")}
                >
                  {LABEL[s]}
                </button>
              );
            })}
          </div>
        </div>
        <p className="mt-1.5 line-clamp-2 min-h-[2lh] text-hint text-faint">{locked(segment === "off" ? "paper" : segment) ?? BLURB[segment]}</p>
        {error && <p className="mt-2 text-hint text-down">{error}</p>}
      </section>
      <CondorBotSettings open={settingsOpen} onClose={() => setSettingsOpen(false)} bot={bot} config={config} readOnly={readOnly} />
    </>
  );
}

function CondorBotSettings({
  open,
  onClose,
  bot,
  config,
  readOnly,
}: {
  open: boolean;
  onClose: () => void;
  bot: Bot;
  config: CondorBotConfig;
  readOnly: boolean;
}) {
  const update = useUpdateBot();
  const [draft, setDraft] = useState(config);
  const [error, setError] = useState<string | null>(null);
  return (
    <Modal open={open} onClose={onClose} titleId="condor-bot-settings" zIndexClass="z-[110]"
      panelClassName="app-card mx-auto !w-[min(96vw,52rem)] !max-w-none max-h-[90vh] overflow-y-auto p-5">
      <div className="space-y-4">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 id="condor-bot-settings" className="app-text-heading">Dynamic Iron Condor bot</h2>
            <p className="text-xs text-muted">
              Changing any campaign setting or the exit action needs a new backtest before the bot can run again, and
              hands its current campaign back to you.
            </p>
          </div>
          <button type="button" className="app-btn-secondary" onClick={onClose}>
            Close
          </button>
        </div>
        <CondorSettingsForm value={draft.campaign} onChange={(campaign) => setDraft({ ...draft, campaign })} disabled={readOnly} />
        <div className="grid gap-3 text-sm sm:grid-cols-3">
          <label className="flex flex-col gap-1">
            At exit DTE
            <select className="app-input" value={draft.exit_action} onChange={(ev) => setDraft({ ...draft, exit_action: ev.target.value as CondorBotConfig["exit_action"] })}>
              <option value="time_roll">Time-roll to the next cycle</option>
              <option value="close">Close</option>
            </select>
          </label>
          <label className="flex flex-col gap-1" title="Blank sizes each tranche from today's margin.">
            Lots per tranche (blank = from margin)
            <input className="app-input font-mono" type="number" min={1} value={draft.lots_per_tranche ?? ""}
              onChange={(ev) => setDraft({ ...draft, lots_per_tranche: ev.target.value ? Number(ev.target.value) : null })} />
          </label>
          <label className="flex flex-col gap-1">
            Approval window (minutes)
            <input className="app-input font-mono" type="number" min={2} max={60} value={draft.proposal_ttl_minutes}
              onChange={(ev) => setDraft({ ...draft, proposal_ttl_minutes: Number(ev.target.value) || 15 })} />
          </label>
        </div>
        {error ? <p className="text-sm text-down">{error}</p> : null}
        <button
          type="button"
          className="app-btn-primary"
          disabled={readOnly || update.isPending}
          onClick={async () => {
            setError(null);
            try {
              await update.mutateAsync({
                botType: bot.bot_type,
                config: {
                  campaign: draft.campaign,
                  exit_action: draft.exit_action,
                  lots_per_tranche: draft.lots_per_tranche,
                  proposal_ttl_minutes: draft.proposal_ttl_minutes,
                },
              });
              onClose();
            } catch (err) {
              setError((err as Error)?.message ?? "Could not save.");
            }
          }}
        >
          Save settings
        </button>
      </div>
    </Modal>
  );
}
