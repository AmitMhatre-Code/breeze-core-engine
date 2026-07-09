/**
 * Shared Tailwind tokens for Strategy Builder / Place Order / Basket Order form
 * controls, wired to the Terminal Pro CSS-variable tokens in globals.css (no raw
 * hex/zinc/sky, no card shadows or glassmorphism per the design spec's hard don'ts).
 */
export const sb = {
  section: "rounded-[13px] border border-border bg-panel p-5",
  sectionTitle:
    "text-[11px] font-bold uppercase tracking-[.07em] text-faint",
  fieldLabel:
    "mb-1.5 block text-[10.5px] font-semibold uppercase tracking-[.06em] text-faint",
  fieldRow: "flex items-center gap-3",
  fieldLabelInline:
    "shrink-0 text-[10.5px] font-semibold uppercase tracking-[.06em] text-faint",
  select:
    "w-full min-w-[10rem] cursor-pointer rounded-[9px] border border-border bg-panel2 px-3.5 py-2.5 font-mono text-sm text-foreground outline-none transition hover:border-accent focus:border-accent focus:ring-2 focus:ring-accent/25 disabled:cursor-not-allowed disabled:opacity-50",
  input:
    "w-full rounded-t-[3px] border-0 border-b border-muted bg-background dark:bg-elevated px-3.5 py-2.5 font-mono text-sm text-foreground outline-none transition placeholder:text-faint hover:border-accent focus:border-accent-strong focus:bg-panel disabled:cursor-not-allowed disabled:opacity-50",
  inputNarrow:
    "w-20 rounded-t-[3px] border-0 border-b border-muted bg-background dark:bg-elevated px-2.5 py-2 font-mono text-sm text-foreground outline-none transition hover:border-accent focus:border-accent-strong focus:bg-panel",
  checkbox:
    "size-4 shrink-0 cursor-pointer rounded-md border-border text-accent-strong transition focus:ring-2 focus:ring-accent/35 focus:ring-offset-0 focus:ring-offset-transparent",
  checkboxRow: "flex cursor-pointer items-center gap-2.5 select-none",
  range:
    "h-2 w-full cursor-pointer appearance-none rounded-full bg-track accent-accent-strong",
  outlookGroup: "flex flex-wrap gap-2",
  outlookChip:
    "rounded-lg border px-4 py-2 text-xs font-medium transition focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/40",
  outlookChipOn: "border-accent bg-accent-tint font-semibold text-foreground",
  outlookChipOff:
    "border-border bg-panel text-muted hover:border-accent/40 hover:bg-panel2",
  stickyBar: "rounded-[13px] border border-border bg-panel px-4 py-3",
  btnPrimary:
    "inline-flex items-center justify-center rounded-lg bg-accent-strong px-4 py-2.5 text-sm font-bold text-accent-ink transition hover:brightness-[1.06] focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/45 disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50",
  btnSecondary:
    "inline-flex items-center justify-center rounded-lg border border-border bg-transparent px-3.5 py-2 text-xs font-semibold text-foreground transition hover:bg-border-soft focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/25 disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50",
  btnDanger:
    "inline-flex items-center justify-center rounded-lg bg-down-btn px-3.5 py-2 text-sm font-bold text-white transition hover:brightness-[1.06] focus:outline-none focus-visible:ring-2 focus-visible:ring-down/40 disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50",
  tableSelect:
    "max-w-[8.5rem] cursor-pointer rounded-[7px] border border-border bg-panel2 px-2 py-1.5 font-mono text-xs text-foreground outline-none focus:border-accent focus:ring-2 focus:ring-accent/20",
  tableInput:
    "w-16 rounded-t-[2px] border-0 border-b border-muted bg-background dark:bg-elevated px-2 py-1.5 font-mono text-xs tabular-nums text-foreground outline-none transition hover:border-accent focus:border-accent-strong focus:bg-panel",
  tableToggle:
    "rounded-[7px] border border-border bg-panel2 px-2.5 py-1 font-mono text-xs font-semibold text-foreground transition hover:bg-border-soft focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/30",
  cardTemplate:
    "rounded-[13px] border border-border bg-panel text-left text-xs text-foreground transition hover:border-accent/50 disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50",
  cardTemplateAmber:
    "rounded-[13px] border border-amber-accent/40 bg-amber-tint text-left text-xs text-foreground transition hover:border-amber-accent/70 disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50",
  modalPanel:
    "max-h-[90vh] w-full space-y-4 overflow-y-auto rounded-[13px] border border-border bg-elevated p-5 shadow-pop",
  radioRow:
    "flex cursor-pointer items-center gap-2.5 text-xs font-medium text-muted",
  radio: "size-4 shrink-0 border-border text-accent-strong focus:ring-accent/30",
  parameterCard:
    "flex h-full flex-col gap-4 rounded-[13px] border border-border bg-panel p-4",
  parameterCardTitle: "text-[13.5px] font-bold text-foreground",
} as const;
