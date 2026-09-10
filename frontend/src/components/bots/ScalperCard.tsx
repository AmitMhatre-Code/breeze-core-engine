"use client";

import { useState } from "react";
import { BotSettingsDrawer } from "@/components/bots/BotSettingsDrawer";
import { BotStatusRow } from "@/components/bots/BotStatusRow";
import { LiveConfirmDialog } from "@/components/bots/LiveConfirmDialog";
import { NumberInput } from "@/components/ui/NumberInput";
import { formatIndianMoneyCompact, moneyToneClass } from "@/lib/format-money-in";
import { describeFeed, feedToneClass } from "@/lib/scalper-audit";
import {
  BOT_META,
  isScalper,
  useBots,
  useLiveEligibility,
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

// Each blurb is written to fit two lines at the 22rem card width — the card clamps to two
// and holds that height, so anything longer is silently cut.
const MODE_BLURB: Record<ScalperCardMode, string> = {
  off: "Not running. No signals are evaluated and nothing is recorded.",
  paper: "Runs on live prices but places no orders. Fills are simulated at the touch, with slippage and charges.",
  live: "Places real orders on the exchange, unattended, within the limits you set.",
};

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
  liveLocked,
  liveLockedReason,
  onChange,
}: {
  mode: ScalperCardMode;
  disabled: boolean;
  /** The paper-evidence gate's answer. Live is not selectable until this bot has completed
   *  a paper trading day on its current settings — the same check the server enforces on
   *  PATCH, read from the same source, so the card can never offer a control that would
   *  then be refused. */
  liveLocked: boolean;
  liveLockedReason: string | null;
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
        const blocked = value === "live" && liveLocked;
        return (
          <button
            key={value}
            type="button"
            aria-pressed={active}
            disabled={disabled || blocked}
            title={blocked ? liveLockedReason ?? undefined : undefined}
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

  const [confirmLiveOpen, setConfirmLiveOpen] = useState(false);

  const mode = cardMode(bot);
  const { data: eligibility } = useLiveEligibility(bot.bot_type);
  // Cycles are read even when the bot is Off: a real position outlives the switch that
  // opened it, and the card has to be able to say so.
  const { data: cycles } = useTodaysCycles(bot.bot_type, true, bot.enabled);
  const { data: todaysRun } = useTodaysRun(bot.bot_type, bot.enabled);
  const feed = describeFeed(todaysRun?.detail ?? null);

  // A switched-off bot still holding a REAL position is not idle — the loop keeps ticking it
  // for its exits until it is flat. Saying "Off" alone there would be a lie about money that
  // is still at risk.
  const closingOut =
    mode === "off" && (cycles ?? []).some((c) => c.closed_at === null && c.paper === false);
  const liveLocked = !eligibility?.unlocked;

  // Separate stops mean the deployment's real daily downside is the SUM of the two, not the
  // number set on either (plan section 5.2). Shown in the dialog only when the other scalper
  // is ALSO live — otherwise it would inflate the figure with a bot that cannot lose today.
  const { data: allBots } = useBots();
  const sibling = (allBots ?? []).find(
    (b) => isScalper(b.bot_type) && b.bot_type !== bot.bot_type,
  );
  const siblingIsLive =
    Boolean(sibling?.enabled) &&
    (sibling?.config as { mode?: ScalperMode } | undefined)?.mode === "live";
  const ownCap =
    ((bot.config as { risk?: { cumulative_stop_inr?: number } }).risk?.cumulative_stop_inr) ?? 0;
  const combinedDownside = siblingIsLive
    ? ownCap +
      (((sibling?.config as { risk?: { cumulative_stop_inr?: number } })?.risk
        ?.cumulative_stop_inr) ?? 0)
    : null;

  const closed = (cycles ?? []).filter((c) => c.closed_at !== null);
  const net = closed.reduce((sum, c) => sum + (c.net_pnl ?? 0), 0);
  // Friction is shown next to P&L, not buried in it. At roughly a hundred rupees a cycle it
  // is what decides whether this strategy works, and a bot that ran twenty cycles to stand
  // still should say so on its face rather than in a report nobody opens.
  const friction = (cycles ?? []).reduce((sum, c) => sum + (c.friction ?? 0), 0);

  async function applyMode(next: ScalperCardMode) {
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
      setConfirmLiveOpen(false);
    } catch (e) {
      setError((e as Error)?.message ?? "Could not save.");
    }
  }

  function setMode(next: ScalperCardMode) {
    if (next === mode) return;
    setError(null);
    // Only the step INTO live is confirmed. Not Paper (which places nothing), not Off, and
    // not Live → Paper — that is the user stepping back, and putting a dialog in front of it
    // would train them to click through dialogs on this control.
    if (next === "live") {
      setConfirmLiveOpen(true);
      return;
    }
    void applyMode(next);
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
      {/* `h-full` + the grid's default stretch — matches BotCard so all four cards in a row
          share one height. The mode-switch cluster is `mt-auto`-pinned to the bottom so it
          lines up with the writer cards' even though this card carries an extra feed-status
          line they don't. */}
      <section className="app-card flex h-full flex-col p-4">
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
            {/* Two lines reserved whether the blurb fills them or not, so the status row
                starts at the same Y as the writer cards. */}
            <p className="app-text-muted mt-1 line-clamp-2 min-h-[2lh] text-hint">{meta.blurb}</p>
          </div>
          <button
            type="button"
            aria-label={`${meta.title} settings`}
            onClick={() => setSettingsOpen(true)}
            className="grid size-8 shrink-0 place-items-center rounded-lg text-muted transition hover:text-accent focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/45"
          >
            <GearIcon />
          </button>
        </div>

        <div className="mt-4 flex flex-col gap-1.5">
          <BotStatusRow
            tone={
              closingOut
                ? "guarded"
                : mode === "live"
                  ? "live"
                  : mode === "paper"
                    ? "guarded"
                    : "idle"
            }
            label={closingOut ? "Closing" : mode === "off" ? "Idle" : "Armed"}
            badge={closingOut ? "Live position" : mode === "paper" ? "Paper" : undefined}
          />
          {/* Two lines reserved so the summary rows below line up with the writer cards'. */}
          <p className="line-clamp-2 min-h-[2lh] font-mono text-hint text-muted">
            {closingOut
              ? "Switched off with a real position still open. Opening nothing new; managing this one to its exit."
              : windowSummary(bot)}
          </p>
          <dl className="mt-3 grid gap-1.5">
            <div className="flex items-baseline justify-between gap-3 text-hint">
              <dt className="text-faint">Cycles today</dt>
              <dd className="m-0 font-mono tabular-nums text-text">{(cycles ?? []).length}</dd>
            </div>
            {/* P&L and friction on one line: friction is roughly a hundred rupees a cycle and
                is what decides whether the strategy clears its own costs, so it rides right
                next to the number it eats into rather than in a row of its own. */}
            <div className="flex items-baseline justify-between gap-3 text-hint">
              <dt className="text-faint">Net P&L</dt>
              <dd className="m-0 font-mono tabular-nums">
                {closed.length ? (
                  <>
                    <span className={moneyToneClass(net)}>{formatIndianMoneyCompact(net)}</span>
                    {friction ? (
                      <span className="text-faint"> · {formatIndianMoneyCompact(friction)} fr</span>
                    ) : null}
                  </>
                ) : (
                  <span className="text-text">—</span>
                )}
              </dd>
            </div>
          </dl>
          {feed && (
            /* Why nothing is happening. Sits below the stats, not above them, so the
               Cycles / Net P&L rows start at the same Y as the writer cards' Expiry /
               Indices rows — this line has no counterpart there, so it can only go where
               the writer cards carry slack: after the shared block. A scalper can sit at
               `not_warm` for a whole session and look healthy otherwise; this is the line
               that says whether it is filling or was never subscribed. */
            <p className={`mt-2 font-mono text-hint ${feedToneClass(feed.tone)}`}>{feed.text}</p>
          )}
        </div>

        {/* `mt-auto`: the mode-switch cluster is pinned to the bottom so it lines up with
            the writer cards' regardless of the feed-status line above, which the writers
            have no counterpart for. See BotCard for the full reasoning. */}
        <div className="mt-auto pt-4">
          <ModePill
            mode={mode}
            disabled={readOnly || update.isPending}
            liveLocked={liveLocked}
            liveLockedReason={eligibility?.blocked_reason ?? null}
            onChange={setMode}
          />
        </div>
        {/* Stacked in one grid cell with the inactive blurbs `invisible` rather than
            unmounted, so the control above never shifts under the cursor mid-choice. Same
            reasoning as BotCard: every blurb is written to two lines and clamped to two, so
            `min-h-[2lh]` fixes the block height and matches the writer cards. */}
        <div className="mt-1.5 grid min-h-[2lh]">
          {(["off", "paper", "live"] as const).map((value) => (
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
        {/* No "Start a run": a scalper's decision is a signal on a one-minute candle, so
            there is nothing a manual run could mean. The honest control is the mode switch
            above, and the run log below is where its work shows up. The writer cards no
            longer carry a bottom button either (their run trigger moved to the header play
            icon), so nothing needs reserving here for them to line up against. */}
        {error && <p className="mt-2 text-hint text-down">{error}</p>}
      </section>

      <BotSettingsDrawer
        bot={bot}
        open={settingsOpen}
        readOnly={readOnly}
        onClose={() => setSettingsOpen(false)}
      />
      <LiveConfirmDialog
        open={confirmLiveOpen}
        botTitle={meta.title}
        evidence={eligibility}
        dailyLossCapInr={
          ((bot.config as { risk?: { cumulative_stop_inr?: number } }).risk
            ?.cumulative_stop_inr) ?? 0
        }
        combinedDownsideInr={combinedDownside}
        pending={update.isPending}
        error={error}
        onConfirm={() => void applyMode("live")}
        onCancel={() => {
          setConfirmLiveOpen(false);
          setError(null);
        }}
      />
    </>
  );
}
