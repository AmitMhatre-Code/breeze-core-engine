import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type RefObject,
} from "react";

type UseListboxComboboxArgs<T> = {
  open: boolean;
  options: T[];
  onSelect: (item: T) => void;
  onClose: () => void;
  /** Extra list roots (e.g. mobile portal) for scroll-into-view. */
  extraListRefs?: Array<RefObject<HTMLUListElement | null>>;
};

/** Arrow / Enter keyboard nav and blur-to-close for constrained combobox inputs. */
export function useListboxCombobox<T>({
  open,
  options,
  onSelect,
  onClose,
  extraListRefs = [],
}: UseListboxComboboxArgs<T>) {
  const [highlightIndex, setHighlightIndex] = useState(-1);
  const listRef = useRef<HTMLUListElement>(null);

  useEffect(() => {
    if (!open) setHighlightIndex(-1);
  }, [open]);

  useEffect(() => {
    setHighlightIndex(-1);
  }, [options]);

  useEffect(() => {
    if (highlightIndex < 0) return;
    const selector = `[data-combobox-index="${highlightIndex}"]`;
    const roots = [listRef, ...extraListRefs];
    for (const ref of roots) {
      ref.current?.querySelector(selector)?.scrollIntoView({ block: "nearest" });
    }
  }, [highlightIndex, extraListRefs]);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLInputElement>) => {
      if (!open) return;

      if (e.key === "Tab") {
        onClose();
        return;
      }

      if (e.key === "ArrowDown") {
        e.preventDefault();
        if (options.length === 0) return;
        setHighlightIndex((i) => (i < options.length - 1 ? i + 1 : 0));
        return;
      }

      if (e.key === "ArrowUp") {
        e.preventDefault();
        if (options.length === 0) return;
        setHighlightIndex((i) => (i > 0 ? i - 1 : options.length - 1));
        return;
      }

      if (e.key === "Enter") {
        if (options.length === 0) return;
        e.preventDefault();
        const idx = highlightIndex >= 0 ? highlightIndex : 0;
        onSelect(options[idx]!);
      }
    },
    [open, options, highlightIndex, onSelect, onClose],
  );

  return { highlightIndex, listRef, handleKeyDown };
}

/** Close the listbox when focus leaves the combobox (defer so option mousedown can run). */
export function useComboboxBlurClose(
  rootRef: RefObject<HTMLElement | null>,
  extraRefs: Array<RefObject<HTMLElement | null>>,
  onClose: () => void,
) {
  return useCallback(() => {
    window.setTimeout(() => {
      const active = document.activeElement;
      if (rootRef.current?.contains(active)) return;
      for (const ref of extraRefs) {
        if (ref.current?.contains(active)) return;
      }
      onClose();
    }, 0);
  }, [rootRef, extraRefs, onClose]);
}
