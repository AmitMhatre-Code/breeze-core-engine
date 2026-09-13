"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { BotSettingsDrawer } from "@/components/bots/BotSettingsDrawer";
import { BotRunSheet } from "@/components/bots/BotRunSheet";
import { BotStatusRow } from "@/components/bots/BotStatusRow";
import { CasBingoCard } from "@/components/bots/CasBingoCard";
import { ScalperCard } from "@/components/bots/ScalperCard";
import { NumberInput } from "@/components/ui/NumberInput";
import {
  fetchTelegramStatus,
  TELEGRAM_STATUS_QUERY_KEY,
} from "@/lib/telegram/telegram-alerts";
import {
  BOT_HOLDINGS_WRITER,
  BOT_META,
  isScalper,
  INDEX_LABEL,
  useBotRuns,
  useUpdateBot,
  type Bot,
  type BotRun,
  type BotRunStatus,
  type ApprovalMode,
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

function PlayIcon() {
  // viewBox cropped to the triangle's own bounds (x 8–19, y 5–19) so the glyph fills the
  // size-4 box the way the gear does — at the full 0 0 24 24 viewBox the triangle only
  // covered ~60% of the height and read as a smaller icon next to the gear.
  return (
    <svg viewBox="8 5 11 14" fill="currentColor" className="size-4" aria-hidden>
      <path d="M8 5v14l11-7z" />
    </svg>
  );
}

/** Header icon buttons (play, gear) share one look: a plain icon, no box or border, colour
 *  the only hover affordance. `size-8` keeps a comfortable hit target around the 16px glyph. */
const HEADER_ICON_BTN =
  "grid size-8 shrink-0 place-items-center rounded-lg text-muted transition hover:text-accent focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/45 disabled:pointer-events-none disabled:opacity-40";

/** The three ways a bot can be left, as one value.
 *
 *  The backend keeps this as two fields — `enabled` arms the scheduler, `approval_mode`
 *  decides what an unattended run does when it has sized a trade. They are only
 *  independent on paper: `approval_mode` is read nowhere but the scheduler, so a disabled
 *  bot's copy of it is dormant and the pair collapses to three states a user can act on.
 *  Deriving the mode here rather than storing a third column keeps that collapse in the
 *  one place that needs it. */
type BotMode = "manual" | "semi" | "auto";

const MODE_LABEL: Record<BotMode, string> = {
  manual: "Manual",
  semi: "Semi-auto",
  auto: "Auto",
};

/** Why the subtext is not optional: "Semi-auto" and "Auto" name a *degree* of automation,
 *  which says nothing about the only difference that matters — whether real orders can be
 *  placed while you are not looking. One line, always visible, says it outright. */
const MODE_BLURB: Record<BotMode, string> = {
  manual: "Runs only when you start a run here. It never fires on its own.",
  semi: "Sizes the trade on schedule and asks on Telegram — nothing is placed until you approve.",
  auto: "Sizes and places the trade on schedule, without waiting for your approval.",
};

function botMode(bot: Bot): BotMode {
  if (!bot.enabled) return "manual";
  const mode = (bot.config as { approval_mode?: ApprovalMode }).approval_mode;
  // Defaults to asking, exactly as the backend model does: an unrecognised or missing
  // value must never resolve to the setting that trades unattended.
  return mode === "auto" ? "auto" : "semi";
}

/** The enable control. A three-word segmented switch rather than a knob, because the state
 *  has to be readable at a glance on a control that arms unattended trading — "which side
 *  is the dot on?" is not a question worth asking about a bot that places real orders. */
function ModePill({
  mode,
  disabled,
  telegramConnected,
  onChange,
}: {
  mode: BotMode;
  disabled: boolean;
  telegramConnected: boolean;
  onChange: (next: BotMode) => void;
}) {
  return (
    <div
      className="grid grid-cols-3 gap-[3px] rounded-full border border-border bg-panel2 p-[3px]"
      role="group"
      aria-label="Bot mode"
    >
      {(["manual", "semi", "auto"] as const).map((value) => {
        const active = mode === value;
        // Semi-auto asks on Telegram, so with no chat linked it cannot ask and therefore
        // cannot trade — it would sit proposing into the void and read as a broken bot.
        // Refusing the selection is honest; a warning after the fact is not.
        const blocked = value === "semi" && !telegramConnected;
        return (
          <button
            key={value}
            type="button"
            aria-pressed={active}
            disabled={disabled || blocked}
            title={
              blocked
                ? "Link a Telegram chat in Settings › Telegram Alerts — semi-auto has no way to ask you without one."
                : undefined
            }
            onClick={() => onChange(value)}
            className={[
              "rounded-full px-1.5 py-1.5 text-micro font-bold uppercase tracking-[0.03em] transition",
              "focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/45",
              "disabled:pointer-events-none disabled:opacity-50",
              active
                ? value === "auto"
                  ? "bg-up-btn text-up-ink"
                  : value === "semi"
                    ? "bg-amber-accent text-amber-ink"
                    : "bg-elevated text-text"
                : "text-faint hover:text-text",
            ].join(" ")}
          >
            {MODE_LABEL[value]}
          </button>
        );
      })}
    </div>
  );
}

/** What the bot will do next, in the user's terms. A bot that says only "enabled" leaves
 *  the one question that matters — when does this actually trade? — unanswered. The verb
 *  tracks the mode: a semi-auto bot does not fire on its schedule, it *asks* on it, and
 *  saying "fires" would promise a trade the user still has to authorise. */
function nextAction(bot: Bot, mode: BotMode): string {
  if (bot.bot_type === BOT_HOLDINGS_WRITER) {
    const config = bot.config as unknown as HoldingsWriterConfig;
    const days = config.fire_days_before_expiry;
    const when = days === 0 ? "on expiry day" : `${days} trading day${days === 1 ? "" : "s"} before expiry`;
    if (mode === "manual") return `Would fire ${when}`;
    const verb = mode === "semi" ? "Asks" : "Fires";
    return `${verb} ${when}, from ${config.nag_start_ist}`;
  }
  const config = bot.config as unknown as ExpiryIndexWriterConfig;
  const indices = Object.entries(config.indices ?? {})
    .filter(([, leg]) => leg.enabled)
    .map(([code]) => INDEX_LABEL[code] ?? code);
  if (indices.length === 0) return "No index enabled";
  if (mode === "manual") return `Would trade ${indices.join(", ")} expiry days`;
  const suffix = mode === "semi" ? ", asks first" : "";
  return `${indices.join(", ")} expiry days, from ${config.nag_start_ist}${suffix}`;
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
  // The scalpers have a different state axis — paper vs live, not who approves — so they
  // get their own card rather than a third meaning bolted onto `botMode`. Delegating here
  // keeps one entry point for the page while the two shapes stay honest about themselves.
  if (isScalper(bot.bot_type)) {
    return <ScalperCard bot={bot} readOnly={readOnly} />;
  }
  // CAS Bingo is its own shape too: Manual / Simulation / Autonomous, no Telegram approval,
  // and a manual sheet that prices five structures rather than approving one proposal.
  if (bot.bot_type === "cas_bingo") {
    return <CasBingoCard bot={bot} readOnly={readOnly} />;
  }
  return <WriterCard bot={bot} readOnly={readOnly} />;
}

function WriterCard({ bot, readOnly }: { bot: Bot; readOnly: boolean }) {
  const meta = BOT_META[bot.bot_type];
  const update = useUpdateBot();
  // Identical args to the run log's own query, so react-query serves both from one fetch.
  const { data: runs } = useBotRuns();
  const lastRun = runs?.find((run) => run.bot_type === bot.bot_type);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [runOpen, setRunOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Shared query key with the settings screen, so both cards and any open drawer resolve
  // from one fetch. Read here rather than in the drawer because the mode control — the
  // only thing that cares whether a chat exists — now lives on the card.
  const telegram = useQuery({
    queryKey: TELEGRAM_STATUS_QUERY_KEY,
    queryFn: fetchTelegramStatus,
  });
  const telegramConnected = Boolean(telegram.data?.connected && telegram.data?.alerts_enabled);

  const mode = botMode(bot);

  async function setMode(next: BotMode) {
    if (next === mode) return;
    setError(null);
    try {
      // Both fields in one PATCH: the backend merges `config`, and sending them separately
      // would leave a visible window in which the bot is armed on the *previous* approval
      // mode — i.e. briefly authorised to place unattended when the user asked for the
      // opposite. `approval_mode` is written even for Manual so the stored value always
      // matches what the card claims, rather than lying dormant at some older setting that
      // takes effect the moment the bot is armed again.
      await update.mutateAsync({
        botType: bot.bot_type,
        enabled: next !== "manual",
        config: { approval_mode: next === "auto" ? "auto" : "telegram" },
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

  return (
    <>
      {/* `h-full` + the grid's default stretch keeps the four cards in a row the same
          height as each other; the mode-switch cluster below is `mt-auto`-pinned to the
          bottom so all four switches line up on one row whatever sits above them. The
          lines above the switch (blurb, schedule) are still clamped to a fixed height so
          the table-like rows read across — the pin just absorbs the one thing that isn't
          shared: the scalper cards' feed-status line. */}
      <section className="app-card flex h-full flex-col p-4">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            {/* One chip, not a chip plus a spinner: the number is editable inside the
                badge so the card's top line reads as a single label. */}
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
            {/* Height reserved for two lines whether the blurb fills them or not, so the
                status row below starts at the same Y on every bot card. */}
            <p className="app-text-muted mt-1 line-clamp-2 min-h-[2lh] text-hint">{meta.blurb}</p>
          </div>
          <div className="flex shrink-0 items-center gap-0.5">
            <button
              type="button"
              aria-label={`Start a run for ${meta.title}`}
              disabled={readOnly}
              onClick={() => setRunOpen(true)}
              className={HEADER_ICON_BTN}
            >
              <PlayIcon />
            </button>
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
            tone={mode === "auto" ? "live" : mode === "semi" ? "guarded" : "idle"}
            label={mode === "manual" ? "Idle" : "Armed"}
            badge={mode === "semi" ? "Asks first" : undefined}
          />
          {/* Two lines reserved whether the sentence fills them or not, so the summary
              rows below start at the same Y on every card and read across as a table. */}
          <p className="line-clamp-2 min-h-[2lh] font-mono text-hint text-muted">
            {nextAction(bot, mode)}
          </p>
          <dl className="mt-3 grid gap-1.5">
            {summaryRows(bot, lastRun).map(([label, value]) => (
              <div key={label} className="flex items-baseline justify-between gap-3 text-hint">
                <dt className="text-faint">{label}</dt>
                <dd className="m-0 font-mono tabular-nums text-text">{value}</dd>
              </div>
            ))}
          </dl>
        </div>

        {/* `mt-auto`: the mode-switch cluster is pinned to the bottom of the card, so all
            four cards in a row land their switches on one line however much (or little)
            content sits above — the scalper cards carry a feed-status line the writers
            don't, and relying on every row above being an equal fixed height is what made
            this drift the last two times. Slack now collects between the stats and here. */}
        <div className="mt-auto pt-4">
          <ModePill
            mode={mode}
            disabled={readOnly || update.isPending}
            telegramConnected={telegramConnected}
            onChange={(next) => void setMode(next)}
          />
        </div>
        {/* Under the control, not above it: the sentence describes what the selected
            segment does, so it has to sit where the eye lands after choosing.

            All three blurbs are rendered stacked in one grid cell and the inactive two are
            hidden with `invisible` (which still reserves layout) rather than unmounted.
            Every blurb is written to fit two lines at the 22rem card width and clamped to
            two, so `min-h-[2lh]` holds the block at exactly that height — the mode switch
            above it never shifts as you click between segments, and it lands at the same Y
            on writers and scalpers alike (`lh` scales with line height, unlike a rem). */}
        <div className="mt-1.5 grid min-h-[2lh]">
          {(["manual", "semi", "auto"] as const).map((value) => (
            <p
              key={value}
              aria-hidden={value !== mode}
              className={`col-start-1 row-start-1 line-clamp-2 text-hint text-faint ${
                value === mode ? "" : "invisible"
              }`}
            >
              {MODE_BLURB[value]}
            </p>
          ))}
        </div>
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
