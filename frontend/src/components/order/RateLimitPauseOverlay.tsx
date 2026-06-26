"use client";

/** Full-screen overlay while waiting out an ICICI 429 rate limit. */
export function RateLimitPauseOverlay(props: {
  secondsRemaining: number;
  title?: string;
}) {
  const { secondsRemaining, title = "Broker rate limit" } = props;
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
      <div className="text-3xl font-semibold tabular-nums text-white dark:text-zinc-50">
        {secondsRemaining}s
      </div>
      <p className="max-w-xs text-xs text-white/80 dark:text-zinc-400">
        Pausing before retrying. You can change the wait duration under Settings → API Usage.
      </p>
    </div>
  );
}
