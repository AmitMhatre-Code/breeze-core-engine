"use client";

import { useState } from "react";
import { BotSettingsDrawer } from "@/components/bots/BotSettingsDrawer";
import { BotRunSheet } from "@/components/bots/BotRunSheet";
import {
  BOT_HOLDINGS_WRITER,
  BOT_META,
  INDEX_LABEL,
  useBotRuns,
  useUpdateBot,
  type Bot,
  type BotRun,
  type BotRunStatus,
  type ExpiryIndexWriterConfig,
  type HoldingsWriterConfig,
} from "@/lib/use-bots";

function GearIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="size-4">
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.6 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.6a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
    </svg>
  );
}

/** The enable control. A two-word segmented switch rather than a knob, because the state
 *  has to be readable at a glance on a control that arms unattended trading — "which side
 *  is the dot on?" is not a question worth asking about a bot that places real orders. */
function AutonomousPill({
  enabled,
  disabled,
  onChange,
}: {
  enabled: boolean;
  disabled: boolean;
  onChange: (next: boolean) => void;
}) {
  return (
    <div
      className="grid grid-cols-2 gap-[3px] rounded-full border border-border bg-panel2 p-[3px]"
      role="group"
      aria-label="Autonomous mode"
    >
      {[false, true].map((value) => {
        const active = enabled === value;
        return (
          <button
            key={String(value)}
            type="button"
            aria-pressed={active}
            disabled={disabled}
            onClick={() => onChange(value)}
            className={[
              "rounded-full px-2 py-1.5 text-micro font-bold uppercase tracking-[0.05em] transition",
              "focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/45",
              "disabled:pointer-events-none disabled:opacity-50",
              active
                ? value
                  ? "bg-up-btn text-up-ink"
                  : "bg-elevated text-text"
                : "text-faint hover:text-text",
            ].join(" ")}
          >
            {value ? "Autonomous" : "Manual"}
          </button>
        );
      })}
    </div>
  );
}

/** What the bot will do next, in the user's terms. A bot that says only "enabled" leaves
 *  the one question that matters — when does this actually trade? — unanswered. */
function nextAction(bot: Bot): string {
  if (bot.bot_type === BOT_HOLDINGS_WRITER) {
    const config = bot.config as unknown as HoldingsWriterConfig;
    const days = config.fire_days_before_expiry;
    const when = days === 0 ? "on expiry day" : `${days} trading day${days === 1 ? "" : "s"} before expiry`;
    return bot.enabled
      ? `Fires ${when}, from ${config.nag_start_ist}`
      : `Would fire ${when}`;
  }
  const config = bot.config as unknown as ExpiryIndexWriterConfig;
  const indices = Object.entries(config.indices ?? {})
    .filter(([, leg]) => leg.enabled)
    .map(([code]) => INDEX_LABEL[code] ?? code);
  if (indices.length === 0) return "No index enabled";
  return bot.enabled
    ? `${indices.join(", ")} expiry days, from ${config.nag_start_ist}`
    : `Would trade ${indices.join(", ")} expiry days`;
}

const RUN_WORD: Record<BotRunStatus, string> = {
  completed: "placed",
  proposed: "proposed",
  skipped: "skipped",
  failed: "failed",
  running: "running",
};

/** "28 Aug · 4 placed" — what the bot last did, which is the question a card that says
 *  only "Armed" leaves open. */
function lastRunSummary(run: BotRun | undefined): string {
  if (!run) return "never";
  const when = run.started_at
    ? new Date(`${run.started_at.replace(" ", "T")}+05:30`).toLocaleDateString("en-IN", {
        day: "2-digit",
        month: "short",
      })
    : "—";
  const legs = Array.isArray(run.detail?.legs) ? (run.detail.legs as unknown[]).length : 0;
  // A semi-autonomous bot's `proposed` run is not "it did a thing and stopped" — it is
  // still waiting on the user, and the card is where they will look for the reason their
  // bot has not traded. Say what is actually blocking it.
  const word =
    run.reason_code === "awaiting_approval"
      ? "awaiting your approval"
      : run.reason_code === "approval_timeout"
        ? "no approval — skipped"
        : run.reason_code === "approval_rejected"
          ? "you rejected it"
          : run.reason_code === "approval_unreachable"
            ? "could not reach Telegram"
            : run.status === "completed" && legs > 0
              ? `${legs} placed`
              : (RUN_WORD[run.status] ?? run.status);
  return `${when} · ${word}`;
}

/** Two rows, not three. The drawer holds the full configuration; the card carries only
 *  what changes between glances. */
function summaryRows(bot: Bot, lastRun: BotRun | undefined): Array<[string, string]> {
  if (bot.bot_type === BOT_HOLDINGS_WRITER) {
    const config = bot.config as unknown as HoldingsWriterConfig;
    return [
      ["Expiry", config.expiry_preference === "next" ? "Next month" : "Current month"],
      ["Last run", lastRunSummary(lastRun)],
    ];
  }
  const config = bot.config as unknown as ExpiryIndexWriterConfig;
  const indices = Object.entries(config.indices ?? {})
    .filter(([, leg]) => leg.enabled)
    .map(([code]) => INDEX_LABEL[code] ?? code);
  return [
    ["Indices", indices.join(", ") || "none"],
    ["Last run", lastRunSummary(lastRun)],
  ];
}

export function BotCard({ bot, readOnly }: { bot: Bot; readOnly: boolean }) {
  const meta = BOT_META[bot.bot_type];
  const update = useUpdateBot();
  // Identical args to the run log's own query, so react-query serves both from one fetch.
  const { data: runs } = useBotRuns();
  const lastRun = runs?.find((run) => run.bot_type === bot.bot_type);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [runOpen, setRunOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function setEnabled(next: boolean) {
    if (next === bot.enabled) return;
    setError(null);
    try {
      await update.mutateAsync({ botType: bot.bot_type, enabled: next });
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

  return (
    <>
      <section className="app-card flex aspect-square flex-col p-4 max-sm:aspect-auto">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            {/* One chip, not a chip plus a spinner: the number is editable inside the
                badge so the card's top line reads as a single label. */}
            <span className="inline-flex items-center gap-1.5 rounded border border-gtt/30 bg-gtt-tint px-2 py-0.5 font-mono text-micro font-bold uppercase tracking-[0.06em] text-gtt-on-tint focus-within:ring-2 focus-within:ring-accent/45">
              Priority
              <input
                type="number"
                min={1}
                max={99}
                aria-label={`Priority for ${meta.title}`}
                value={bot.priority}
                disabled={readOnly || update.isPending}
                onChange={(e) => void setPriority(Number(e.target.value))}
                className="w-4 border-0 bg-transparent p-0 text-center font-mono text-micro font-bold text-gtt-on-tint focus:outline-none disabled:opacity-50 [-moz-appearance:textfield] [appearance:textfield] [&::-webkit-inner-spin-button]:m-0 [&::-webkit-inner-spin-button]:appearance-none"
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

        {/* Content flows from the top and the controls are pinned to the bottom, so the
            card's slack collects in ONE place. Centring this block instead put a void
            above AND below it, which is what made the square read as empty. */}
        <div className="mt-4 flex flex-col gap-1.5">
          <div className="flex items-center gap-2">
            <span
              aria-hidden
              className={`size-[7px] rounded-full ${bot.enabled ? "bg-up" : "bg-faint"}`}
            />
            <span
              className={`text-xl font-bold tracking-tight ${
                bot.enabled ? "text-up" : "text-faint"
              }`}
            >
              {bot.enabled ? "Armed" : "Idle"}
            </span>
          </div>
          <p className="font-mono text-hint text-muted">{nextAction(bot)}</p>
          <dl className="mt-3 grid gap-1.5">
            {summaryRows(bot, lastRun).map(([label, value]) => (
              <div key={label} className="flex items-baseline justify-between gap-3 text-hint">
                <dt className="text-faint">{label}</dt>
                <dd className="m-0 font-mono tabular-nums text-text">{value}</dd>
              </div>
            ))}
          </dl>
        </div>

        <div className="flex-1" />

        <AutonomousPill
          enabled={bot.enabled}
          disabled={readOnly || update.isPending}
          onChange={(next) => void setEnabled(next)}
        />
        <button
          type="button"
          className="app-btn-outline mt-2 w-full"
          disabled={readOnly}
          onClick={() => setRunOpen(true)}
        >
          Start a run
        </button>

        {error && <p className="mt-2 text-hint text-down">{error}</p>}
      </section>

      <BotSettingsDrawer
        bot={bot}
        open={settingsOpen}
        readOnly={readOnly}
        onClose={() => setSettingsOpen(false)}
      />
      <BotRunSheet
        bot={bot}
        open={runOpen}
        readOnly={readOnly}
        onClose={() => setRunOpen(false)}
      />
    </>
  );
}
