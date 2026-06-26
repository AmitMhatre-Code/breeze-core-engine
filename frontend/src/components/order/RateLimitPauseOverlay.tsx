"use client";

/** Full-screen overlay while waiting out an ICICI 429/503 rate limit. */
export function RateLimitPauseOverlay(props: {
  secondsRemaining: number;
  title?: string;
  reason?: string;
}) {
  const { secondsRemaining, title = "Broker rate limit", reason } = props;
  return (
    <div
      className="fixed inset-0 z-[200] flex flex-col items-center justify-center gap-4 bg-black/55 p-6 text-center dark:bg-black/70"
      role="status"
      aria-live="polite"
      aria-label={`${title}, resuming in ${secondsRemaining} seconds`}
    >
      <div
        className="h-10 w-10 animate-spin rounded-full border-2 border-white/30 border-t-white dark:border-zinc-600 dark:border-t-zinc-100"
        aria-hidden
      />
      <div className="max-w-sm text-sm font-medium text-white dark:text-zinc-100">
        {title}
      </div>
      {reason ? (
        <p className="max-w-md text-xs leading-relaxed text-white/85 dark:text-zinc-300">
          {reason}
        </p>
      ) : null}
      <div className="text-3xl font-semibold tabular-nums text-white dark:text-zinc-50">
        {secondsRemaining}s
      </div>
      <p className="max-w-xs text-xs text-white/80 dark:text-zinc-400">
        Pausing before the next ICICI API attempt. After a rate limit, spacing uses your
        Settings → API Usage pause value with exponential backoff up to 5 seconds.
      </p>
    </div>
  );
}
