"use client";

import { useState } from "react";
import { BotSettingsDrawer } from "@/components/bots/BotSettingsDrawer";
import { NumberInput } from "@/components/ui/NumberInput";
import { formatIndianMoneyCompact, moneyToneClass } from "@/lib/format-money-in";
import { describeFeed, feedToneClass } from "@/lib/scalper-audit";
import {
  BOT_META,
  useTodaysCycles,
  useTodaysRun,
  useUpdateBot,
  type Bot,
  type IronFlyScalperConfig,
  type MomentumLongScalperConfig,
  type ScalperMode,
} from "@/lib/use-bots";

function GearIcon() {
  return (
    <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden>
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.7 1.7 0 0 0 .34 1.88l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.7 1.7 0 0 0-1.88-.34 1.7 1.7 0 0 0-1 1.56V21a2 2 0 1 1-4 0v-.09A1.7 1.7 0 0 0 8.9 19.3a1.7 1.7 0 0 0-1.88.34l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.7 1.7 0 0 0 4.6 15a1.7 1.7 0 0 0-1.56-1H3a2 2 0 1 1 0-4h.09A1.7 1.7 0 0 0 4.7 8.9a1.7 1.7 0 0 0-.34-1.88l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.7 1.7 0 0 0 9 4.6a1.7 1.7 0 0 0 1-1.56V3a2 2 0 1 1 4 0v.09a1.7 1.7 0 0 0 1 1.56 1.7 1.7 0 0 0 1.88-.34l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.7 1.7 0 0 0 19.4 9V9a1.7 1.7 0 0 0 1.56 1H21a2 2 0 1 1 0 4h-.09a1.7 1.7 0 0 0-1.51 1Z" />
    </svg>
  );
}

/** How a scalper can be left.
 *
 *  A different axis from Bots 1 and 2, which collapse `enabled` + `approval_mode` into
 *  manual/semi/auto. A scalper has no `approval_mode` at all — it never asks before a
 *  trade, because approving dozens of scalps a day is not a workflow — so reusing that
 *  control would render an armed scalper as "Semi-auto" and claim it waits for approval it
 *  will never request. Its real question is narrower and sharper: can this place real
 *  orders, or is it only pretending?
 */
type ScalperCardMode = "off" | "paper" | "live";

const MODE_LABEL: Record<ScalperCardMode, string> = {
  off: "Off",
  paper: "Paper",
  live: "Live",
};

const MODE_BLURB: Record<ScalperCardMode, string> = {
  off: "Not running. No signals are evaluated and nothing is recorded.",
  paper: "Runs the full strategy on live prices and places no orders. Fills are simulated at the touch, with slippage and charges.",
  live: "Places real orders on the exchange, unattended, within the limits you set.",
};

/** Live dispatch is not built yet (build-order step 9, gated on the circuit-breaker tests).
 *  The segment is rendered but not selectable: a switch that looks armed and is not would be
 *  the worst possible state for this particular control. */
const LIVE_AVAILABLE = false;

function cardMode(bot: Bot): ScalperCardMode {
  if (!bot.enabled) return "off";
  const mode = (bot.config as { mode?: ScalperMode }).mode;
  // Anything unrecognised resolves to paper, matching the backend model. An unknown value
  // must never mean "places real orders".
  return mode === "live" ? "live" : "paper";
}

function ModePill({
  mode,
  disabled,
  onChange,
}: {
  mode: ScalperCardMode;
  disabled: boolean;
  onChange: (next: ScalperCardMode) => void;
}) {
  return (
    <div
      className="grid grid-cols-3 gap-[3px] rounded-full border border-border bg-panel2 p-[3px]"
      role="group"
      aria-label="Scalper mode"
    >
      {(["off", "paper", "live"] as const).map((value) => {
        const active = mode === value;
        const blocked = value === "live" && !LIVE_AVAILABLE;
        return (
          <button
            key={value}
            type="button"
            aria-pressed={active}
            disabled={disabled || blocked}
            title={
              blocked
                ? "Live trading is not available yet — the bot runs in paper mode until real order placement ships."
                : undefined
            }
            onClick={() => onChange(value)}
            className={[
              "rounded-full px-1.5 py-1.5 text-micro font-bold uppercase tracking-[0.03em] transition",
              "focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/45",
              "disabled:pointer-events-none disabled:opacity-50",
              active
                ? value === "live"
                  ? "bg-up-btn text-up-ink"
                  : value === "paper"
                    ? "bg-amber-accent text-amber-ink"
                    : "bg-elevated text-text"
                : "text-faint hover:text-text",
            ].join(" ")}
          >
            {MODE_LABEL[value]}
            {blocked && value === "live" ? " ·" : ""}
          </button>
        );
      })}
    </div>
  );
}

/** When the bot actually trades, in the user's terms. A scalper has no "next run": it
 *  evaluates continuously inside its windows, so the honest answer is the windows. */
function windowSummary(bot: Bot): string {
  const config = bot.config as unknown as MomentumLongScalperConfig | IronFlyScalperConfig;
  const windows = config.sessions ?? [];
  if (windows.length === 0) return "No trading window configured.";
  // Up to four windows are configurable, so "A and B and C" needs to become "A, B and C".
  const spans = windows.map((w) => `${w.start}–${w.end}`);
  const list =
    spans.length > 1
      ? `${spans.slice(0, -1).join(", ")} and ${spans[spans.length - 1]}`
      : spans[0];
  return `Trades ${list}, flat by ${config.hard_square_off_ist}.`;
}

export function ScalperCard({ bot, readOnly }: { bot: Bot; readOnly: boolean }) {
  const meta = BOT_META[bot.bot_type];
  const update = useUpdateBot();
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const mode = cardMode(bot);
  const { data: cycles } = useTodaysCycles(bot.bot_type, bot.enabled);
  const { data: todaysRun } = useTodaysRun(bot.bot_type, bot.enabled);
  const feed = describeFeed(todaysRun?.detail ?? null);

  const closed = (cycles ?? []).filter((c) => c.closed_at !== null);
  const net = closed.reduce((sum, c) => sum + (c.net_pnl ?? 0), 0);
  // Friction is shown next to P&L, not buried in it. At roughly a hundred rupees a cycle it
  // is what decides whether this strategy works, and a bot that ran twenty cycles to stand
  // still should say so on its face rather than in a report nobody opens.
  const friction = (cycles ?? []).reduce((sum, c) => sum + (c.friction ?? 0), 0);

  async function setMode(next: ScalperCardMode) {
    if (next === mode) return;
    setError(null);
    try {
      // Both fields in one PATCH. Sent separately there would be a window in which the bot
      // is armed on the *previous* mode — briefly authorised to place real orders when the
      // user asked for paper.
      await update.mutateAsync({
        botType: bot.bot_type,
        enabled: next !== "off",
        config: { mode: next === "live" ? "live" : "paper" },
      });
    } catch (e) {
      setError((e as Error)?.message ?? "Could not save.");
    }
  }

  async function setPriority(next: number) {
    setError(null);
    try {
      await update.mutateAsync({ botType: bot.bot_type, priority: next });
    } catch (e) {
      setError((e as Error)?.message ?? "Could not save.");
    }
  }

  const rows: Array<[string, string, string]> = [
    ["Cycles today", String((cycles ?? []).length), "text-text"],
    ["Net P&L", closed.length ? formatIndianMoneyCompact(net) : "—", closed.length ? moneyToneClass(net) : "text-text"],
    ["Friction", friction ? formatIndianMoneyCompact(friction) : "—", "text-text"],
  ];

  return (
    <>
      <section className="app-card flex aspect-square flex-col p-4 max-sm:aspect-auto">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <span className="inline-flex items-center gap-1.5 rounded border border-gtt/30 bg-gtt-tint px-2 py-0.5 font-mono text-micro font-bold uppercase tracking-[0.06em] text-gtt-on-tint focus-within:ring-2 focus-within:ring-accent/45">
              Priority
              <NumberInput
                min={1}
                max={99}
                aria-label={`Priority for ${meta.title}`}
                value={bot.priority}
                disabled={readOnly || update.isPending}
                onChange={(v) => void setPriority(v)}
                className="w-5 border-0 bg-transparent p-0 text-center font-mono text-micro font-bold text-gtt-on-tint focus:outline-none disabled:opacity-50 [-moz-appearance:textfield] [appearance:textfield] [&::-webkit-inner-spin-button]:m-0 [&::-webkit-inner-spin-button]:appearance-none"
              />
            </span>
            <h2 className="app-text-heading mt-1.5">{meta.title}</h2>
            <p className="app-text-muted mt-1 text-hint">{meta.blurb}</p>
          </div>
          <button
            type="button"
            aria-label={`${meta.title} settings`}
            onClick={() => setSettingsOpen(true)}
            className="grid size-8 shrink-0 place-items-center rounded-lg border border-border bg-panel2 text-muted transition hover:border-accent/45 hover:text-accent focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/45"
          >
            <GearIcon />
          </button>
        </div>

        <div className="mt-4 flex flex-col gap-1.5">
          <div className="flex items-center gap-2">
            <span
              aria-hidden
              className={`size-[7px] rounded-full ${
                mode === "live" ? "bg-up" : mode === "paper" ? "bg-amber-accent" : "bg-faint"
              }`}
            />
            <span
              className={`text-xl font-bold tracking-tight ${
                mode === "live" ? "text-up" : mode === "paper" ? "text-amber-accent" : "text-faint"
              }`}
            >
              {mode === "off" ? "Idle" : "Armed"}
            </span>
            {/* Unmissable, and deliberately so: a paper bot is otherwise indistinguishable
                from a live one at a glance, which is the whole risk of having both. */}
            {mode === "paper" && (
              <span className="rounded border border-amber-accent/40 bg-amber-tint px-1.5 py-0.5 font-mono text-micro font-bold uppercase tracking-[0.06em] text-amber-accent">
                Paper
              </span>
            )}
          </div>
          <p className="font-mono text-hint text-muted">{windowSummary(bot)}</p>
          {feed && (
            /* Why nothing is happening, where the user is already looking. A scalper can
               sit at `not_warm` for a whole session and look perfectly healthy otherwise;
               this is the line that says whether it is filling or was never subscribed. */
            <p className={`mt-1 font-mono text-hint ${feedToneClass(feed.tone)}`}>{feed.text}</p>
          )}
          <dl className="mt-3 grid gap-1.5">
            {rows.map(([label, value, tone]) => (
              <div key={label} className="flex items-baseline justify-between gap-3 text-hint">
                <dt className="text-faint">{label}</dt>
                <dd className={`m-0 font-mono tabular-nums ${tone}`}>{value}</dd>
              </div>
            ))}
          </dl>
        </div>

        <div className="flex-1" />

        <ModePill
          mode={mode}
          disabled={readOnly || update.isPending}
          onChange={(next) => void setMode(next)}
        />
        {/* Stacked in one grid cell with the inactive blurbs `invisible` rather than
            unmounted, so the block is always as tall as the longest one and the control
            above it never shifts under the cursor mid-choice. Same reasoning as BotCard. */}
        <div className="mt-1.5 grid">
          {(["off", "paper", "live"] as const).map((value) => (
            <p
              key={value}
              aria-hidden={value !== mode}
              className={`col-start-1 row-start-1 text-hint text-faint ${
                value === mode ? "" : "invisible"
              }`}
            >
              {MODE_BLURB[value]}
            </p>
          ))}
        </div>
        {/* No "Start a run": a scalper's decision is a signal on a one-minute candle, so
            there is nothing a manual run could mean. The honest control is the mode switch
            above, and the run log below is where its work shows up. */}

        {error && <p className="mt-2 text-hint text-down">{error}</p>}
      </section>

      <BotSettingsDrawer
        bot={bot}
        open={settingsOpen}
        readOnly={readOnly}
        onClose={() => setSettingsOpen(false)}
      />
    </>
  );
}
