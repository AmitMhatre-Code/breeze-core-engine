"use client";

/** Full-screen overlay while the NIFTY/SENSEX system chains are still warming up. */
export function MarketConnectionOverlay() {
  return (
    <div
      className="fixed inset-0 z-[200] flex flex-col items-center justify-center gap-4 bg-black/55 p-6 text-center dark:bg-black/70"
      role="status"
      aria-live="polite"
      aria-label="Setting up market connection"
    >
      <div
        className="h-10 w-10 animate-spin rounded-full border-2 border-white/30 border-t-white dark:border-zinc-600 dark:border-t-zinc-100"
        aria-hidden
      />
      <div className="max-w-sm text-sm font-medium text-white dark:text-zinc-100">
        Setting up market connection…
      </div>
      <p className="max-w-xs text-xs text-white/80 dark:text-zinc-400">
        Warming up live NIFTY &amp; SENSEX data. This only takes a few seconds.
      </p>
    </div>
  );
}
