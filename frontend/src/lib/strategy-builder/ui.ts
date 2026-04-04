/**
 * Shared Tailwind tokens for Strategy Builder form controls (v4-friendly).
 * Rounded-xl, soft shadow, focus rings, accent colors.
 */
export const sb = {
  section:
    "rounded-xl border border-zinc-200/90 bg-white/95 p-5 shadow-sm ring-1 ring-zinc-950/[0.03] backdrop-blur-sm dark:border-zinc-800 dark:bg-zinc-900/55 dark:ring-white/[0.06]",
  sectionTitle:
    "text-sm font-semibold tracking-tight text-zinc-900 dark:text-zinc-50",
  fieldLabel:
    "mb-1.5 block text-xs font-medium text-zinc-600 dark:text-zinc-400",
  select:
    "w-full min-w-[10rem] cursor-pointer rounded-lg border border-zinc-200 bg-white px-3.5 py-2.5 text-sm text-zinc-900 shadow-sm outline-none transition hover:border-zinc-300 focus:border-sky-500 focus:ring-4 focus:ring-sky-500/15 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:hover:border-zinc-600 dark:focus:border-sky-400 dark:focus:ring-sky-400/20 disabled:cursor-not-allowed disabled:border-zinc-200 disabled:bg-zinc-100 disabled:text-zinc-400 dark:disabled:border-zinc-700 dark:disabled:bg-zinc-800 dark:disabled:text-zinc-500",
  input:
    "w-full rounded-lg border border-zinc-200 bg-white px-3.5 py-2.5 text-sm text-zinc-900 shadow-sm outline-none transition placeholder:text-zinc-400 hover:border-zinc-300 focus:border-sky-500 focus:ring-4 focus:ring-sky-500/15 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:placeholder:text-zinc-500 dark:hover:border-zinc-600 dark:focus:border-sky-400 dark:focus:ring-sky-400/20 disabled:cursor-not-allowed disabled:border-zinc-200 disabled:bg-zinc-100 disabled:text-zinc-400 dark:disabled:border-zinc-700 dark:disabled:bg-zinc-800 dark:disabled:text-zinc-500",
  inputNarrow:
    "w-20 rounded-lg border border-zinc-200 bg-white px-2.5 py-2 text-sm text-zinc-900 shadow-sm outline-none transition focus:border-sky-500 focus:ring-4 focus:ring-sky-500/15 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:focus:border-sky-400 dark:focus:ring-sky-400/20",
  checkbox:
    "size-4 shrink-0 cursor-pointer rounded-md border-zinc-300 text-sky-600 shadow-sm transition focus:ring-2 focus:ring-sky-500/35 focus:ring-offset-0 focus:ring-offset-transparent dark:border-zinc-600 dark:bg-zinc-950 dark:text-sky-500",
  checkboxRow: "flex cursor-pointer items-center gap-2.5 select-none",
  range:
    "h-2 w-full cursor-pointer appearance-none rounded-full bg-zinc-200 accent-sky-600 dark:bg-zinc-700 dark:accent-sky-500",
  segmentGroup:
    "inline-flex rounded-lg border border-zinc-200/90 bg-zinc-100/90 p-1 shadow-inner dark:border-zinc-700 dark:bg-zinc-950/80",
  segmentBtn:
    "rounded-md px-4 py-2 text-sm font-medium transition focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-500/50",
  segmentBtnActive:
    "bg-white text-zinc-900 shadow-sm ring-1 ring-zinc-200/80 dark:bg-zinc-800 dark:text-zinc-50 dark:ring-zinc-600",
  segmentBtnInactive:
    "text-zinc-600 hover:text-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-100",
  outlookGroup: "flex flex-wrap gap-2",
  outlookChip:
    "rounded-lg border px-4 py-2 text-xs font-medium transition focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-500/40",
  outlookChipOn:
    "border-sky-500 bg-sky-600 text-white shadow-sm dark:border-sky-400 dark:bg-sky-600",
  outlookChipOff:
    "border-zinc-200 bg-white text-zinc-700 shadow-sm hover:border-zinc-300 hover:bg-zinc-50 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-200 dark:hover:border-zinc-600 dark:hover:bg-zinc-800",
  stickyBar:
    "rounded-xl border border-zinc-200/90 bg-zinc-50/90 px-4 py-3 shadow-sm ring-1 ring-zinc-950/[0.03] backdrop-blur-md dark:border-zinc-800 dark:bg-zinc-950/85 dark:ring-white/[0.05]",
  btnPrimary:
    "inline-flex items-center justify-center rounded-lg border border-sky-600 bg-sky-600 px-4 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:border-sky-500 hover:bg-sky-500 focus:outline-none focus-visible:ring-4 focus-visible:ring-sky-500/35 disabled:pointer-events-none disabled:cursor-not-allowed disabled:border-sky-800 disabled:bg-sky-800 dark:border-sky-500 dark:bg-sky-500 dark:hover:border-sky-400 dark:hover:bg-sky-400 dark:disabled:border-sky-800 dark:disabled:bg-sky-800",
  btnSecondary:
    "inline-flex items-center justify-center rounded-lg border border-sky-200 bg-white px-3.5 py-2 text-xs font-medium text-sky-800 shadow-sm transition hover:border-sky-300 hover:bg-sky-50 focus:outline-none focus-visible:ring-4 focus-visible:ring-sky-500/25 disabled:pointer-events-none disabled:cursor-not-allowed disabled:border-zinc-200 disabled:bg-zinc-50 disabled:text-zinc-400 dark:border-sky-800/70 dark:bg-zinc-900 dark:text-sky-200 dark:hover:border-sky-600 dark:hover:bg-sky-950/40 dark:disabled:border-zinc-700 dark:disabled:bg-zinc-900 dark:disabled:text-zinc-500",
  btnDanger:
    "inline-flex items-center justify-center rounded-lg bg-red-600 px-3.5 py-2 text-sm font-semibold text-white shadow-sm transition hover:bg-red-700 focus:outline-none focus-visible:ring-4 focus-visible:ring-red-500/30 disabled:pointer-events-none disabled:cursor-not-allowed disabled:bg-red-800 dark:bg-red-600 dark:hover:bg-red-500 dark:disabled:bg-red-900",
  tableSelect:
    "max-w-[8.5rem] cursor-pointer rounded-md border border-zinc-200 bg-white px-2 py-1.5 text-xs text-zinc-900 shadow-sm outline-none focus:border-sky-500 focus:ring-2 focus:ring-sky-500/20 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100",
  tableInput:
    "w-16 rounded-md border border-zinc-200 bg-white px-2 py-1.5 text-xs tabular-nums text-zinc-900 shadow-sm outline-none focus:border-sky-500 focus:ring-2 focus:ring-sky-500/20 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100",
  tableToggle:
    "rounded-md border border-zinc-200 bg-white px-2.5 py-1 text-xs font-medium text-zinc-800 shadow-sm transition hover:bg-zinc-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-500/30 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100 dark:hover:bg-zinc-800",
  cardTemplate:
    "rounded-lg border border-zinc-200/90 bg-gradient-to-b from-white to-zinc-50/80 p-3.5 text-left text-xs text-zinc-950 shadow-sm transition hover:border-zinc-300 hover:shadow-md disabled:pointer-events-none disabled:cursor-not-allowed disabled:border-zinc-300 disabled:from-zinc-100 disabled:to-zinc-100 disabled:text-zinc-400 dark:border-zinc-700 dark:from-zinc-900 dark:to-zinc-950/80 dark:text-zinc-50 dark:hover:border-zinc-600 dark:disabled:border-zinc-600 dark:disabled:from-zinc-900 dark:disabled:to-zinc-900 dark:disabled:text-zinc-500",
  cardTemplateAmber:
    "rounded-lg border border-amber-200/90 bg-gradient-to-b from-amber-50 to-amber-50/30 p-3.5 text-left text-xs text-zinc-950 shadow-sm transition hover:border-amber-300 hover:shadow-md disabled:pointer-events-none disabled:cursor-not-allowed disabled:border-amber-200 disabled:from-amber-100/80 disabled:to-amber-100/50 disabled:text-amber-900/50 dark:border-amber-900/40 dark:from-amber-950/50 dark:to-amber-950/20 dark:text-zinc-50 dark:hover:border-amber-800/60 dark:disabled:border-amber-900/60 dark:disabled:from-amber-950/40 dark:disabled:to-amber-950/40 dark:disabled:text-zinc-500",
  modalPanel:
    "max-h-[90vh] w-full space-y-4 overflow-y-auto rounded-xl border border-zinc-200/90 bg-white p-5 shadow-2xl ring-1 ring-zinc-950/5 dark:border-zinc-800 dark:bg-zinc-900 dark:ring-white/10",
  radioRow:
    "flex cursor-pointer items-center gap-2.5 text-xs font-medium text-zinc-700 dark:text-zinc-300",
  radio:
    "size-4 shrink-0 border-zinc-300 text-sky-600 focus:ring-sky-500/30 dark:border-zinc-600 dark:bg-zinc-950",
} as const;
