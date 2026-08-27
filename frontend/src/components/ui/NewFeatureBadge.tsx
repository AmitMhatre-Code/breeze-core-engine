export function NewFeatureBadge() {
  return (
    <span
      aria-hidden
      // --accent-ink is defined against --accent-strong (that is the pairing
      // .app-btn-primary and sb.btnPrimary use); on the lighter --accent it was
      // white-on-#0891b2 at 3.68:1. Dark theme is unaffected — both tokens are #22d3ee there.
      className="inline-flex shrink-0 items-center rounded-[4px] bg-accent-strong px-[5px] py-[1.5px] text-micro font-bold uppercase tracking-[0.06em] text-accent-ink"
    >
      New
    </span>
  );
}
