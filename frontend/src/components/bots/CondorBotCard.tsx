"use client";

import { useId, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { BacktestButton } from "@/components/bots/BacktestButton";
import { BotSettingsDrawer } from "@/components/bots/BotSettingsDrawer";
import { BotStatusRow } from "@/components/bots/BotStatusRow";
import { LastBacktestRow } from "@/components/bots/LastBacktestRow";
import { PriorityPill } from "@/components/bots/PriorityPill";
import type { CondorBotConfig } from "@/components/bots/CondorSettings";
import { CampaignCard } from "@/components/condor/CondorCampaignPanel";
import { Modal } from "@/components/ui/Modal";
import { apiClient } from "@/lib/api-client";
import type { CondorCampaign } from "@/lib/condor";
import { BOT_META, useUpdateBot, type Bot } from "@/lib/use-bots";

/** The Dynamic Iron Condor bot (docs/dynamic-iron-condor-plan.md section 7). Its modes unlock
 *  in order -- a completed backtest of these exact settings, then a paper cycle, then a ticket
 *  approved on Telegram -- and the server refuses whatever the evidence does not yet allow.
 *
 *  The header is every bot's (#67): play opens Basket Orders on a first tranche at these
 *  settings, the clock backtests them into Activity, the gear opens the settings drawer. */

type Overview = {
  eligibility: {
    settings_hash: string;
    backtest: { run_id: string; created_at: string; from: string; to: string } | null;
    paper_cycles: number;
    approved_executions: number;
    may_enable: boolean;
    may_telegram: boolean;
    may_auto: boolean;
  };
  campaign: CondorCampaign | null;
  /** Why an armed bot has not opened its campaign yet, e.g. its expiry is another campaign's. */
  waiting?: string | null;
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

function GearIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="size-4">
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.6 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.6a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
    </svg>
  );
}

function PlayIcon() {
  return (
    <svg viewBox="8 5 11 14" fill="currentColor" className="size-4" aria-hidden>
      <path d="M8 5v14l11-7z" />
    </svg>
  );
}

const HEADER_ICON_BTN =
  "grid size-8 shrink-0 place-items-center rounded-lg text-muted transition hover:text-accent focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/45 disabled:pointer-events-none disabled:opacity-40";

/** The card's two reserved lines under the status: what the bot is doing, or will do next. */
function nextLine(bot: Bot, config: CondorBotConfig, camp: CondorCampaign | null, waiting: string | null): string {
  const c = config.campaign;
  const checks = `checks ${c.sod_check_ist} and ${c.eod_check_ist} IST`;
  if (!bot.enabled) return `Off. ${c.tranches} tranche(s) from ${c.entry_dte} DTE, exit at ${c.exit_dte} DTE.`;
  if (config.paused) return `Paused: decides nothing at its ${checks} until you resume it.`;
  if (camp?.cycle) return `Running NIFTY ${camp.cycle.expiry} (${camp.mode}) · ${checks}.`;
  if (waiting) return `Waiting: ${waiting}`;
  return `Opens a campaign at the next check once a cycle's expiry is in range · ${checks}.`;
}

export function CondorBotCard({ bot, readOnly }: { bot: Bot; readOnly: boolean }) {
  const meta = BOT_META[bot.bot_type];
  const router = useRouter();
  const config = bot.config as unknown as CondorBotConfig;
  const update = useUpdateBot();
  const [error, setError] = useState<string | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [paperOpen, setPaperOpen] = useState(false);
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
    if (!e.may_enable) return "Needs a completed backtest of these settings: the clock above.";
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

  return (
    <>
      <section className="app-card flex h-full flex-col p-4">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <PriorityPill bot={bot} readOnly={readOnly} onError={setError} />
            <h2 className="app-text-heading mt-1.5">{meta.title}</h2>
            <p className="app-text-muted mt-1 line-clamp-2 min-h-[2lh] text-hint">{meta.blurb}</p>
          </div>
          <div className="flex shrink-0 items-center gap-0.5">
            <button
              type="button"
              aria-label={`Start a campaign by hand for ${meta.title}`}
              title="Start a campaign by hand in Basket Orders"
              disabled={readOnly}
              onClick={() => router.push("/basket-order?condor=1")}
              className={HEADER_ICON_BTN}
            >
              <PlayIcon />
            </button>
            <BacktestButton botType={bot.bot_type} className={HEADER_ICON_BTN} />
            <button
              type="button"
              aria-label={`${meta.title} settings`}
              onClick={() => setSettingsOpen(true)}
              className={HEADER_ICON_BTN}
            >
              <GearIcon />
            </button>
          </div>
        </div>

        <div className="mt-4 flex flex-col gap-1.5">
          <BotStatusRow
            tone={!bot.enabled ? "idle" : config.paused ? "guarded" : config.mode === "auto" ? "live" : "guarded"}
            label={!bot.enabled ? "Idle" : config.paused ? "Paused" : "Armed"}
            badge={bot.enabled && config.paused ? "Paused" : bot.enabled && config.mode !== "auto" ? (config.mode === "paper" ? "Paper" : "Asks first") : undefined}
          />
          <p className="line-clamp-2 min-h-[2lh] font-mono text-hint text-muted">{nextLine(bot, config, camp, overview.data?.waiting ?? null)}</p>
          {config.paused ? (
            <div className="flex items-center justify-between gap-2 text-hint">
              <span className="line-clamp-2 text-amber-on-tint">Paused: {config.paused_reason ?? "by you"}</span>
              <button type="button" className="app-btn-secondary" disabled={readOnly || update.isPending} onClick={resume}>
                Resume
              </button>
            </div>
          ) : null}
          <dl className="mt-3 grid gap-1.5">
            <div className="flex items-baseline justify-between gap-3 text-hint">
              <dt className="text-faint">Campaign</dt>
              <dd className="m-0 font-mono tabular-nums text-text">
                {!camp ? (
                  "—"
                ) : camp.mode === "paper" ? (
                  // A paper campaign owns no Portfolio row, so its card opens here.
                  <button type="button" className="app-link" onClick={() => setPaperOpen(true)}>
                    paper · {camp.cycle?.expiry ?? "—"}
                  </button>
                ) : (
                  <Link className="app-link" href="/portfolio">
                    live · {camp.cycle?.expiry ?? "—"}
                  </Link>
                )}
              </dd>
            </div>
            <div className="flex items-baseline justify-between gap-3 text-hint">
              <dt className="text-faint">Paper cycles · approved</dt>
              <dd className="m-0 font-mono tabular-nums text-text">
                {e?.paper_cycles ?? 0} · {e?.approved_executions ?? 0}
              </dd>
            </div>
            <LastBacktestRow botType={bot.bot_type} />
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
      <BotSettingsDrawer bot={bot} open={settingsOpen} readOnly={readOnly} onClose={() => setSettingsOpen(false)} />
      {camp && camp.mode === "paper" ? (
        <PaperCampaignDrawer campaign={camp} open={paperOpen} onClose={() => setPaperOpen(false)} />
      ) : null}
    </>
  );
}

/** The bot's paper campaign: the same card a live one shows on its Portfolio group. */
function PaperCampaignDrawer({ campaign, open, onClose }: { campaign: CondorCampaign; open: boolean; onClose: () => void }) {
  const titleId = useId();
  return (
    <Modal open={open} onClose={onClose} variant="drawer" drawerSide="right" drawerWidthClass="w-[min(100%,52rem)]" titleId={titleId}>
      <div className="flex items-start justify-between gap-3 border-b border-border p-4">
        <div>
          <h2 id={titleId} className="text-subtitle font-bold">
            Paper campaign · NIFTY {campaign.cycle?.expiry ?? "—"}
          </h2>
          <p className="app-text-muted mt-1 text-hint">Filled at live prices on paper. Nothing here is placed.</p>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close paper campaign"
          className="rounded p-1 text-faint transition hover:text-text focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/45"
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="size-4">
            <path d="M18 6 6 18M6 6l12 12" />
          </svg>
        </button>
      </div>
      <div className="flex-1 overflow-auto p-4">
        <CampaignCard campaign={campaign} request={null} />
      </div>
    </Modal>
  );
}
