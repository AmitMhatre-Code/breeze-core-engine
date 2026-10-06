"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import { Checkbox } from "@/components/ui/Checkbox";

const PANEL_WIDTH = 240;

const ClosePanel = createContext<() => void>(() => {});

/** Closes the open header panel, from inside it. */
export function useClosePanel(): () => void {
  return useContext(ClosePanel);
}
const VIEWPORT_MARGIN = 8;

function FunnelIcon({ active }: { active: boolean }) {
  return (
    <svg
      width="12"
      height="12"
      viewBox="0 0 24 24"
      fill={active ? "currentColor" : "none"}
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="M3 4h18l-7 8.5V19l-4 2v-8.5z" />
    </svg>
  );
}

/** A table header that opens a filter panel, as an Excel column does.
 *
 *  The panel is portalled to the body and fixed-positioned: the table sits in a horizontally
 *  scrolling wrapper, which would clip anything absolutely positioned inside a header cell.
 *  `children` is rendered only while open, so a panel's draft state starts fresh on every open
 *  and closing without OK (click outside, Escape, Cancel) discards it. A panel closes itself
 *  with `useClosePanel()`. */
export function HeaderFilter({
  label,
  summary,
  active,
  children,
}: {
  label: string;
  /** Shown beside the label, so a filter that is always set (the date range) is never hidden. */
  summary?: string;
  active: boolean;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const panelId = useId();

  const close = useCallback(() => {
    setOpen(false);
    setPos(null);
    triggerRef.current?.focus();
  }, []);

  const reposition = useCallback(() => {
    const trigger = triggerRef.current;
    if (!trigger) return;
    const rect = trigger.getBoundingClientRect();
    const height = panelRef.current?.offsetHeight ?? 0;
    const roomBelow = window.innerHeight - rect.bottom - VIEWPORT_MARGIN;
    const above = height > roomBelow && rect.top - VIEWPORT_MARGIN > roomBelow;
    setPos({
      top: above ? Math.max(VIEWPORT_MARGIN, rect.top - height - 4) : rect.bottom + 4,
      left: Math.max(
        VIEWPORT_MARGIN,
        Math.min(rect.left, window.innerWidth - PANEL_WIDTH - VIEWPORT_MARGIN),
      ),
    });
  }, []);

  useLayoutEffect(() => {
    if (open) reposition();
  }, [open, reposition]);

  useEffect(() => {
    if (!open) return;
    panelRef.current?.focus();
    const onDoc = (e: MouseEvent) => {
      const target = e.target as Node;
      if (triggerRef.current?.contains(target) || panelRef.current?.contains(target)) return;
      close();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    window.addEventListener("scroll", reposition, true);
    window.addEventListener("resize", reposition);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("scroll", reposition, true);
      window.removeEventListener("resize", reposition);
    };
  }, [open, close, reposition]);

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        onClick={() => (open ? close() : setOpen(true))}
        className="inline-flex items-center gap-1.5 whitespace-nowrap rounded text-left transition hover:text-accent focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/45"
      >
        {label}
        {summary && (
          <span className="rounded bg-panel px-1.5 py-0.5 font-mono text-[11px] font-semibold text-muted">
            {summary}
          </span>
        )}
        <span className={active ? "text-accent" : "text-faint"}>
          <FunnelIcon active={active} />
        </span>
        {active && <span className="sr-only">(filtered)</span>}
      </button>
      {open && typeof document !== "undefined"
        ? createPortal(
            <div
              ref={panelRef}
              id={panelId}
              role="dialog"
              aria-label={`Filter ${label}`}
              tabIndex={-1}
              className="fixed z-50 rounded-[10px] border border-border bg-elevated p-2 text-left text-xs font-normal shadow-pop focus:outline-none"
              style={{
                width: PANEL_WIDTH,
                top: pos?.top ?? -9999,
                left: pos?.left ?? -9999,
                visibility: pos ? "visible" : "hidden",
              }}
            >
              <ClosePanel.Provider value={close}>{children}</ClosePanel.Provider>
            </div>,
            document.body,
          )
        : null}
    </>
  );
}

/** Cancel discards the draft; OK applies it, then closes. */
export function FilterPanelFooter({ onOk, okDisabled }: { onOk: () => void; okDisabled?: boolean }) {
  const close = useClosePanel();
  return (
    <div className="mt-2 flex justify-end gap-2 border-t border-border-soft pt-2">
      <button type="button" className="app-btn-outline py-1" onClick={close}>
        Cancel
      </button>
      <button
        type="button"
        className="app-btn-primary px-3 py-1 text-xs"
        disabled={okDisabled}
        onClick={() => {
          onOk();
          close();
        }}
      >
        OK
      </button>
    </div>
  );
}

export type FilterOption = { value: string; label: ReactNode; count: number };

/** A multi-select panel: Select all, one box per value with its count, OK / Cancel. Every
 *  value ever possible is listed, not only those in range, so a selection survives a change of
 *  range; a value with nothing in range is greyed but can still be ticked. OK needs at least
 *  one value — an empty table is not a filter anyone means. */
function ValueFilterPanel({
  options,
  selected,
  onApply,
}: {
  options: FilterOption[];
  selected: readonly string[] | null;
  onApply: (next: string[] | null) => void;
}) {
  const all = options.map((o) => o.value);
  const [draft, setDraft] = useState<Set<string>>(() => new Set(selected ?? all));
  const total = options.reduce((sum, o) => sum + o.count, 0);
  const allTicked = draft.size === all.length;

  const toggle = (value: string, on: boolean) =>
    setDraft((d) => {
      const next = new Set(d);
      if (on) next.add(value);
      else next.delete(value);
      return next;
    });

  const apply = () => onApply(allTicked ? null : all.filter((v) => draft.has(v)));

  return (
    <>
      <label className="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 hover:bg-border-soft">
        <Checkbox
          checked={allTicked}
          indeterminate={draft.size > 0 && !allTicked}
          onChange={(on) => setDraft(new Set(on ? all : []))}
        />
        <span className="flex-1 font-semibold">Select all</span>
        <span className="tabular-nums text-faint">{total}</span>
      </label>
      <div className="my-1 border-t border-border-soft" />
      <ul className="max-h-64 overflow-y-auto">
        {options.map((o) => (
          <li key={o.value}>
            <label
              className={`flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 hover:bg-border-soft ${
                o.count === 0 ? "text-faint" : ""
              }`}
            >
              <Checkbox checked={draft.has(o.value)} onChange={(on) => toggle(o.value, on)} />
              <span className="flex-1">{o.label}</span>
              <span className="tabular-nums text-faint">{o.count}</span>
            </label>
          </li>
        ))}
      </ul>
      <FilterPanelFooter onOk={apply} okDisabled={draft.size === 0} />
    </>
  );
}

export function ValueFilterHeader({
  label,
  options,
  selected,
  onApply,
}: {
  label: string;
  options: FilterOption[];
  selected: readonly string[] | null;
  onApply: (next: string[] | null) => void;
}) {
  return (
    <HeaderFilter label={label} active={selected != null}>
      <ValueFilterPanel options={options} selected={selected} onApply={onApply} />
    </HeaderFilter>
  );
}
