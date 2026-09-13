"use client";

import { useRef, useState } from "react";
import { BotSettingsDrawer } from "@/components/bots/BotSettingsDrawer";
import { BotStatusRow } from "@/components/bots/BotStatusRow";
import { CasBingoSheet } from "@/components/bots/CasBingoSheet";
import { Modal } from "@/components/ui/Modal";
import { NumberInput } from "@/components/ui/NumberInput";
import { formatIndianMoneyCompact, moneyToneClass } from "@/lib/format-money-in";
import {
  BOT_META,
  CAS_BINGO_CREDIT_WARNING,
  CAS_BINGO_REGIME_NOTE,
  INDEX_LABEL,
  useSignalReadiness,
  useTodaysCycles,
  useTodaysRun,
  useUpdateBot,
  type Bot,
  type CasBingoConfig,
  type CasBingoStrategy,
} from "@/lib/use-bots";

/** CAS Bingo's three modes (docs/bots-cas-bingo-plan.md section 1).
 *
 *  Its own axis again: no Telegram approval (the user dropped HITL for this bot — a flip
 *  inside a ~15-minute window cannot wait on a message), and no paper-evidence gate on
 *  Autonomous. Manual is `enabled=false`; the run sheet works in every mode. */
type CasCardMode = "manual" | "simulation" | "live";

const MODE_LABEL: Record<CasCardMode, string> = {
  manual: "Manual",
  simulation: "Simulation",
  live: "Autonomous",
};

// Two lines at the 22rem card width, like the other cards' blurbs.
const MODE_BLURB: Record<CasCardMode, string> = {
  manual: "Never fires on its own. Run it from the sheet and pick a structure yourself.",
  simulation: "Runs the full logic on live prices on expiry days. Places no orders.",
  live: "Places real orders on expiry days when its trigger fires, within your limits.",
};

export const CAS_STRATEGY_LABEL: Record<CasBingoStrategy, string> = {
  credit_spread: "Credit spread",
  debit_spread: "Debit spread",
  long_strangle: "Long strangle",
};

const READINESS_LABEL: Record<string, string> = {
  ready: "ready",
  too_early: "too early",
  no_edge: "no edge",
  worse: "worse than trend",
};

function GearIcon() {
  return (
    <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden>
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.7 1.7 0 0 0 .34 1.88l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.7 1.7 0 0 0-1.88-.34 1.7 1.7 0 0 0-1 1.56V21a2 2 0 1 1-4 0v-.09A1.7 1.7 0 0 0 8.9 19.3a1.7 1.7 0 0 0-1.88.34l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.7 1.7 0 0 0 4.6 15a1.7 1.7 0 0 0-1.56-1H3a2 2 0 1 1 0-4h.09A1.7 1.7 0 0 0 4.7 8.9a1.7 1.7 0 0 0-.34-1.88l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.7 1.7 0 0 0 9 4.6a1.7 1.7 0 0 0 1-1.56V3a2 2 0 1 1 4 0v.09a1.7 1.7 0 0 0 1 1.56 1.7 1.7 0 0 0 1.88-.34l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.7 1.7 0 0 0 19.4 9V9a1.7 1.7 0 0 0 1.56 1H21a2 2 0 1 1 0 4h-.09a1.7 1.7 0 0 0-1.51 1Z" />
    </svg>
  );
}

function cardMode(bot: Bot): CasCardMode {
  if (!bot.enabled) return "manual";
  // Anything unrecognised resolves to simulation, matching the backend default. An unknown
  // value must never mean "places real orders".
  return (bot.config as { mode?: string }).mode === "live" ? "live" : "simulation";
}

function scheduleSummary(config: CasBingoConfig): string {
  const indices = Object.entries(config.indices ?? {})
    .filter(([, v]) => v.enabled)
    .map(([code]) => INDEX_LABEL[code] ?? code);
  if (indices.length === 0) return "No index selected.";
  const strategy = CAS_STRATEGY_LABEL[config.strategy] ?? config.strategy;
  return `${strategy} · ${indices.join(" & ")} expiry days, ${config.pre_cas_window.start}–${config.cas_window.end}.`;
}

function AutonomousConfirm({
  open,
  config,
  readiness,
  pending,
  error,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  config: CasBingoConfig;
  readiness: Record<string, { status: string }> | undefined;
  pending: boolean;
  error: string | null;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  // Focus starts on Cancel: the safe choice is the one a stray Enter takes.
  const cancelRef = useRef<HTMLButtonElement>(null);
  const spreads = config.strategy !== "long_strangle";
  const indices = Object.entries(config.indices ?? {}).filter(([, v]) => v.enabled).map(([c]) => c);
  return (
    <Modal
      open={open}
      onClose={onCancel}
      pending={pending}
      role="alertdialog"
      titleId="cas-bingo-confirm-title"
      initialFocusRef={cancelRef}
      panelClassName="w-full max-w-lg rounded-xl border-2 border-down bg-panel p-5 shadow-pop"
    >
      <h2 id="cas-bingo-confirm-title" className="text-lg font-bold uppercase tracking-wide text-down">
        Enable Autonomous?
      </h2>
      <div className="mt-3 space-y-3 text-sm leading-relaxed text-text">
        <p>
          <strong>CAS Bingo</strong> will place <strong>real orders</strong> on expiry days — a{" "}
          {CAS_STRATEGY_LABEL[config.strategy].toLowerCase()}, buy leg first — whenever its
          trigger fires inside your windows. It does not ask before each trade.
        </p>
        {spreads && (
          <div className="rounded-lg border border-border bg-panel2 p-3 text-hint">
            <p className="text-faint">
              Spread entries wait for the signal&apos;s readiness verdict to be <em>ready</em>:
            </p>
            <ul className="mt-1.5 space-y-1">
              {indices.map((code) => {
                const label = code === "BSESEN" ? "sensex" : "nifty";
                const status = readiness?.[label]?.status ?? "unknown";
                return (
                  <li key={code} className="flex justify-between gap-3">
                    <span>{INDEX_LABEL[code] ?? code}</span>
                    <span className={`font-mono ${status === "ready" ? "text-up" : "text-amber-on-tint"}`}>
                      {READINESS_LABEL[status] ?? status}
                    </span>
                  </li>
                );
              })}
            </ul>
          </div>
        )}
        {config.strategy === "credit_spread" && (
          <p className="rounded-lg border border-down/30 bg-down-tint p-3 text-hint">
            <strong>Warning:</strong> {CAS_BINGO_CREDIT_WARNING}
          </p>
        )}
        <p className="text-hint text-faint">{CAS_BINGO_REGIME_NOTE}</p>
        {error && <p className="text-hint text-down">{error}</p>}
      </div>
      <div className="mt-5 flex justify-end gap-2">
        <button ref={cancelRef} type="button" disabled={pending} onClick={onCancel} className="app-btn-outline px-4 py-2.5 text-sm">
          Cancel
        </button>
        <button
          type="button"
          disabled={pending}
          onClick={onConfirm}
          className="inline-flex items-center justify-center rounded-lg bg-down-btn px-4 py-2.5 text-sm font-bold text-down-ink transition hover:brightness-[1.06] focus:outline-none focus-visible:ring-2 focus-visible:ring-down/40 disabled:pointer-events-none disabled:opacity-50"
        >
          {pending ? "Enabling…" : "Enable Autonomous"}
        </button>
      </div>
    </Modal>
  );
}

export function CasBingoCard({ bot, readOnly }: { bot: Bot; readOnly: boolean }) {
  const meta = BOT_META[bot.bot_type];
  const config = bot.config as unknown as CasBingoConfig;
  const update = useUpdateBot();
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [sheetOpen, setSheetOpen] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const mode = cardMode(bot);
  const { data: cycles } = useTodaysCycles(bot.bot_type, true, bot.enabled);
  const { data: todaysRun } = useTodaysRun(bot.bot_type, bot.enabled);
  const { data: readiness } = useSignalReadiness(config.strategy !== "long_strangle");

  const open = (cycles ?? []).filter((c) => c.closed_at === null);
  const closed = (cycles ?? []).filter((c) => c.closed_at !== null);
  const net = closed.reduce((sum, c) => sum + (c.net_pnl ?? 0), 0);
  // A position outlives the switch that opened it: the loop manages it to its exit.
  const holding = open.some((c) => c.paper === false);

  async function applyMode(next: CasCardMode) {
    setError(null);
    try {
      // One PATCH, both fields: sent separately there would be a moment armed on the
      // previous mode — briefly authorised for real orders when the user asked for less.
      await update.mutateAsync({
        botType: bot.bot_type,
        enabled: next !== "manual",
        config: { mode: next === "live" ? "live" : "simulation" },
      });
      setConfirmOpen(false);
    } catch (e) {
      setError((e as Error)?.message ?? "Could not save.");
    }
  }

  function setMode(next: CasCardMode) {
    if (next === mode) return;
    if (next === "live") {
      setConfirmOpen(true);
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

  const readinessLine =
    config.strategy === "long_strangle" || !readiness
      ? null
      : Object.entries(config.indices ?? {})
          .filter(([, v]) => v.enabled)
          .map(([code]) => {
            const status = readiness.indices?.[code === "BSESEN" ? "sensex" : "nifty"]?.status ?? "unknown";
            return `${INDEX_LABEL[code] ?? code} ${READINESS_LABEL[status] ?? status}`;
          })
          .join(" · ");

  return (
    <>
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
            tone={holding ? "guarded" : mode === "live" ? "live" : mode === "simulation" ? "guarded" : "idle"}
            label={holding && mode === "manual" ? "Closing" : mode === "manual" ? "Idle" : "Armed"}
            badge={holding ? "Live position" : mode === "simulation" ? "Simulation" : undefined}
          />
          <p className="line-clamp-2 min-h-[2lh] font-mono text-hint text-muted">{scheduleSummary(config)}</p>
          <dl className="mt-3 grid gap-1.5">
            <div className="flex items-baseline justify-between gap-3 text-hint">
              <dt className="text-faint">Positions today</dt>
              <dd className="m-0 font-mono tabular-nums text-text">
                {(cycles ?? []).length}
                {open.length ? <span className="text-faint"> · {open.length} open</span> : null}
              </dd>
            </div>
            <div className="flex items-baseline justify-between gap-3 text-hint">
              <dt className="text-faint">Net P&L</dt>
              <dd className="m-0 font-mono tabular-nums">
                {closed.length ? (
                  <span className={moneyToneClass(net)}>{formatIndianMoneyCompact(net)}</span>
                ) : (
                  <span className="text-text">—</span>
                )}
              </dd>
            </div>
          </dl>
          {readinessLine && (
            <p className="mt-1 font-mono text-hint text-faint">Signal readiness: {readinessLine}</p>
          )}
          {todaysRun?.reason_text && (
            <p className="mt-1 line-clamp-2 font-mono text-hint text-muted" title={todaysRun.reason_text}>
              {todaysRun.reason_text}
            </p>
          )}
        </div>

        <div className="mt-auto pt-4">
          <div className="grid grid-cols-3 gap-[3px] rounded-full border border-border bg-panel2 p-[3px]" role="group" aria-label="CAS Bingo mode">
            {(["manual", "simulation", "live"] as const).map((value) => {
              const active = mode === value;
              return (
                <button
                  key={value}
                  type="button"
                  aria-pressed={active}
                  disabled={readOnly || update.isPending}
                  onClick={() => setMode(value)}
                  className={[
                    "rounded-full px-1.5 py-1.5 text-micro font-bold uppercase tracking-[0.03em] transition",
                    "focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/45",
                    "disabled:pointer-events-none disabled:opacity-50",
                    active
                      ? value === "live"
                        ? "bg-up-btn text-up-ink"
                        : value === "simulation"
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
        </div>
        <div className="mt-1.5 grid min-h-[2lh]">
          {(["manual", "simulation", "live"] as const).map((value) => (
            <p
              key={value}
              aria-hidden={value !== mode}
              className={`col-start-1 row-start-1 line-clamp-2 text-hint text-faint ${value === mode ? "" : "invisible"}`}
            >
              {MODE_BLURB[value]}
            </p>
          ))}
        </div>
        <button
          type="button"
          className="app-btn-outline mt-2 w-full"
          disabled={readOnly}
          onClick={() => setSheetOpen(true)}
        >
          Run manually
        </button>
        {error && <p className="mt-2 text-hint text-down">{error}</p>}
      </section>

      <BotSettingsDrawer bot={bot} open={settingsOpen} readOnly={readOnly} onClose={() => setSettingsOpen(false)} />
      <CasBingoSheet open={sheetOpen} readOnly={readOnly} onClose={() => setSheetOpen(false)} />
      <AutonomousConfirm
        open={confirmOpen}
        config={config}
        readiness={readiness?.indices}
        pending={update.isPending}
        error={error}
        onConfirm={() => void applyMode("live")}
        onCancel={() => {
          setConfirmOpen(false);
          setError(null);
        }}
      />
    </>
  );
}
