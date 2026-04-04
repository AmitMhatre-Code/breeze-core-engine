/**
 * Visible when NEXT_PUBLIC_ICICI_BROKER_MODE=mock (set in repo-root .env for local dev).
 */
export function MockBrokerBanner() {
  if (process.env.NEXT_PUBLIC_ICICI_BROKER_MODE !== "mock") {
    return null;
  }
  return (
    <div
      role="status"
      className="border-b border-amber-500/60 bg-amber-500/10 px-4 py-2 text-center text-sm text-amber-950 dark:text-amber-100"
    >
      Mock broker — no real orders or live ICICI calls.
    </div>
  );
}
