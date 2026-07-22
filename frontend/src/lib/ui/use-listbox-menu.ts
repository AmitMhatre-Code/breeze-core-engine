import {
  useCallback,
  useEffect,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type RefObject,
} from "react";

type UseListboxMenuArgs = {
  open: boolean;
  optionCount: number;
  onOpen: () => void;
  onClose: () => void;
  onSelectIndex: (index: number) => void;
  triggerRef: RefObject<HTMLElement | null>;
  listRef: RefObject<HTMLElement | null>;
};

/** Keyboard nav for button-triggered listbox menus (not input comboboxes). */
export function useListboxMenu({
  open,
  optionCount,
  onOpen,
  onClose,
  onSelectIndex,
  triggerRef,
  listRef,
}: UseListboxMenuArgs) {
  const [highlightIndex, setHighlightIndex] = useState(-1);

  useEffect(() => {
    if (!open) setHighlightIndex(-1);
  }, [open]);

  useEffect(() => {
    setHighlightIndex(-1);
  }, [optionCount]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.key === "Escape") {
        onClose();
        triggerRef.current?.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose, triggerRef]);

  useEffect(() => {
    if (highlightIndex < 0) return;
    listRef.current
      ?.querySelector(`[data-menu-index="${highlightIndex}"]`)
      ?.scrollIntoView({ block: "nearest" });
  }, [highlightIndex, listRef]);

  const focusOption = useCallback(
    (index: number) => {
      const options = listRef.current?.querySelectorAll<HTMLElement>(
        "[data-menu-index]",
      );
      options?.[index]?.focus();
    },
    [listRef],
  );

  const handleTriggerKeyDown = useCallback(
    (e: ReactKeyboardEvent<HTMLElement>) => {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        if (!open) {
          onOpen();
          const idx = e.key === "ArrowDown" ? 0 : Math.max(0, optionCount - 1);
          setHighlightIndex(idx);
          requestAnimationFrame(() => focusOption(idx));
          return;
        }
      }
      if (!open) return;
      if (e.key === "ArrowDown") {
        e.preventDefault();
        const next =
          highlightIndex < optionCount - 1 ? highlightIndex + 1 : 0;
        setHighlightIndex(next);
        focusOption(next);
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        const next =
          highlightIndex > 0 ? highlightIndex - 1 : optionCount - 1;
        setHighlightIndex(next);
        focusOption(next);
      } else if (e.key === "Enter" && highlightIndex >= 0) {
        e.preventDefault();
        onSelectIndex(highlightIndex);
      }
    },
    [open, optionCount, highlightIndex, onOpen, onSelectIndex, focusOption],
  );

  return { highlightIndex, setHighlightIndex, handleTriggerKeyDown };
}

export function useListboxOutsideClose(
  open: boolean,
  rootRef: RefObject<HTMLElement | null>,
  onClose: () => void,
) {
  useEffect(() => {
    if (!open) return;
    const fn = (e: MouseEvent) => {
      if (rootRef.current?.contains(e.target as Node)) return;
      onClose();
    };
    document.addEventListener("mousedown", fn);
    return () => document.removeEventListener("mousedown", fn);
  }, [open, onClose, rootRef]);
}
