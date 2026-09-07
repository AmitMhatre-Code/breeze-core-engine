/** The one row every bot card shares: a coloured dot, the state word, and — only when the
 *  bot is armed but guarded — a pill naming the guard.
 *
 *  Writers (Bots 1–2) and scalpers (Bots 3–4) sit on different state axes
 *  (manual/semi/auto vs off/paper/live) but the card has to answer the same question the
 *  same way in the same place: is this armed, and if so is anything actually reaching the
 *  exchange? Three tones cover both:
 *
 *    - `live`    — trades unattended (Auto / Live). Green, no pill.
 *    - `guarded` — armed, but nothing reaches the exchange on its own (Semi-auto asks first;
 *                  Paper simulates). Amber, with a pill saying which.
 *    - `idle`    — not armed (Manual / Off). Faint, no pill.
 *
 *  Semi-auto is deliberately NOT green: colouring "asks first" the same as unattended
 *  trading would flatten the one distinction the card exists to make. Same for Paper — a
 *  paper bot is otherwise indistinguishable from a live one at a glance, which is the whole
 *  risk of running both. */
type BotStatusTone = "live" | "guarded" | "idle";

const TONE: Record<BotStatusTone, { dot: string; text: string }> = {
  live: { dot: "bg-up", text: "text-up" },
  guarded: { dot: "bg-amber-accent", text: "text-amber-accent" },
  idle: { dot: "bg-faint", text: "text-faint" },
};

export function BotStatusRow({
  tone,
  label,
  badge,
}: {
  tone: BotStatusTone;
  /** "Armed" or "Idle". */
  label: string;
  /** "Asks first" (semi-auto writers) or "Paper" (paper scalpers). Only rendered on the
   *  `guarded` tone; anything else is a bug at the call site. */
  badge?: string;
}) {
  const t = TONE[tone];
  return (
    <div className="flex items-center gap-2">
      <span aria-hidden className={`size-[7px] rounded-full ${t.dot}`} />
      <span className={`text-xl font-bold tracking-tight ${t.text}`}>{label}</span>
      {badge && (
        <span className="rounded border border-amber-accent/40 bg-amber-tint px-1.5 py-0.5 font-mono text-micro font-bold uppercase tracking-[0.06em] text-amber-accent">
          {badge}
        </span>
      )}
    </div>
  );
}
