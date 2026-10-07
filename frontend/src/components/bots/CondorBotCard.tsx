"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { BacktestButton } from "@/components/bots/BacktestButton";
import { BotSettingsDrawer } from "@/components/bots/BotSettingsDrawer";
import { BotStatusRow } from "@/components/bots/BotStatusRow";
import { PriorityPill } from "@/components/bots/PriorityPill";
import type { CondorBotConfig } from "@/components/bots/CondorSettings";
import { apiClient } from "@/lib/api-client";
import { formatIndianMoneyCompact, moneyToneClass } from "@/lib/format-money-in";
import type { CondorCampaign } from "@/lib/condor";
import { fetchTelegramStatus, TELEGRAM_STATUS_QUERY_KEY } from "@/lib/telegram/telegram-alerts";
import { BOT_META, useUpdateBot, type Bot } from "@/lib/use-bots";

/** The Dynamic Iron Condor bot (docs/dynamic-iron-condor-plan.md section 7). Its modes carry the
 *  same names as every bot's (#70): Simulation is open from day 1, like the others; Semi-auto
 *  unlocks after a Simulation cycle on these settings and Auto after a ticket approved in
 *  Semi-auto. The server refuses whatever the evidence does not yet allow. The stored values
 *  stay `paper` and `telegram`.
 *
 *  The header is every bot's (#67): play opens Basket Orders on a first tranche at these
 *  settings, the clock backtests them into Activity, the gear opens the settings drawer.
 *
 *  The campaign itself is not on the card (#73): it is one row in Activity, running for as long
 *  as the campaign is open, restated at each check and expanding into the campaign card. */

type Overview = {
  eligibility: {
    settings_hash: string;
    /** The newest completed backtest of exactly these settings, a compared combination
     *  included (#71). Shown, never a gate (#70). */
    backtest: {
      run_id: string;
      created_at: string;
      from: string;
      to: string;
      net_pnl?: number | null;
      combination?: string | null;
    } | null;
    paper_cycles: number;
    approved_executions: number;
    may_telegram: boolean;
    may_auto: boolean;
  };
  campaign: CondorCampaign | null;
  /** Why an armed bot has not opened its campaign yet, e.g. its expiry is another campaign's. */
  waiting?: string | null;
};

type Segment = "off" | "paper" | "telegram" | "auto";
const SEGMENTS: Segment[] = ["off", "paper", "telegram", "auto"];
const LABEL: Record<Segment, string> = { off: "Off", paper: "Simulation", telegram: "Semi-auto", auto: "Auto" };
const BLURB: Record<Segment, string> = {
  off: "Decides nothing. A live campaign it ran is handed back to you.",
  paper: "Acts at the two daily checks at live prices, simulated. Places nothing.",
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
  if (camp?.cycle) return `Running NIFTY ${camp.cycle.expiry} (${camp.mode === "paper" ? "simulation" : camp.mode}) · ${checks}.`;
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
  // The same query Bots 1 and 2 read: Semi-auto asks on Telegram, so without a linked chat it
  // could only propose into the void.
  const telegram = useQuery({ queryKey: TELEGRAM_STATUS_QUERY_KEY, queryFn: fetchTelegramStatus });
  const telegramConnected = Boolean(telegram.data?.connected && telegram.data?.alerts_enabled);
  const overview = useQuery({
    queryKey: ["bots", "condor-overview", bot.updated_at],
    queryFn: ({ signal }) => apiClient.get<Overview>("/api/condor/bot", signal),
  });
  const e = overview.data?.eligibility;
  const camp = overview.data?.campaign ?? null;
  const segment: Segment = bot.enabled ? config.mode : "off";

  const locked = (s: Segment): string | null => {
    // Simulation needs no evidence, like every bot's (#70).
    if (s === "off" || s === "paper") return null;
    if (s === "telegram" && !telegramConnected)
      return "Link a Telegram chat in Settings › Telegram Alerts — semi-auto has no way to ask you without one.";
    // Locked until the evidence has loaded: an unknown is never an unlock.
    if (!e) return "Checking what these settings have earned…";
    if (s === "telegram" && !e.may_telegram) return "Unlocks after one Simulation cycle on these settings.";
    if (s === "auto" && !e.may_auto) return "Unlocks after a Simulation cycle and a ticket approved in Semi-auto, on these settings.";
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
            badge={bot.enabled && config.paused ? "Paused" : bot.enabled && config.mode !== "auto" ? (config.mode === "paper" ? "Simulation" : "Asks first") : undefined}
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
              <dt className="text-faint">Simulation cycles · approved</dt>
              <dd className="m-0 font-mono tabular-nums text-text">
                {e?.paper_cycles ?? 0} · {e?.approved_executions ?? 0}
              </dd>
            </div>
            <div className="flex items-baseline justify-between gap-3 text-hint">
              <dt className="text-faint">Backtest of these settings</dt>
              <dd
                className="m-0 font-mono tabular-nums"
                title={
                  e?.backtest
                    ? `${e.backtest.from} → ${e.backtest.to}${e.backtest.combination ? ` · compared as ${e.backtest.combination}` : ""}`
                    : "No completed backtest has replayed these exact settings yet."
                }
              >
                {typeof e?.backtest?.net_pnl === "number" ? (
                  <span className={moneyToneClass(e.backtest.net_pnl)}>{formatIndianMoneyCompact(e.backtest.net_pnl)}</span>
                ) : (
                  <span className="text-text">—</span>
                )}
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
        <p className="mt-1.5 line-clamp-2 min-h-[2lh] text-hint text-faint">{locked(segment) ?? BLURB[segment]}</p>
        {error && <p className="mt-2 text-hint text-down">{error}</p>}
      </section>
      <BotSettingsDrawer bot={bot} open={settingsOpen} readOnly={readOnly} onClose={() => setSettingsOpen(false)} />
    </>
  );
}
