import {
  useCallback,
  useEffect,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type RefObject,
} from "react";

type UseCalendarKeyboardArgs = {
  open: boolean;
  cells: (number | null)[];
  selectedDay: number | null;
  todayDay: number | null;
  onSelectDay: (day: number) => void;
  onClose: () => void;
  triggerRef: RefObject<HTMLElement | null>;
};

function initialFocusIndex(
  cells: (number | null)[],
  selectedDay: number | null,
  todayDay: number | null,
): number {
  if (selectedDay != null) {
    const idx = cells.findIndex((d) => d === selectedDay);
    if (idx >= 0) return idx;
  }
  if (todayDay != null) {
    const idx = cells.findIndex((d) => d === todayDay);
    if (idx >= 0) return idx;
  }
  return cells.findIndex((d) => d != null);
}

function moveFocusIndex(
  cells: (number | null)[],
  from: number,
  delta: number,
): number {
  if (cells.length === 0) return -1;
  let i = from;
  for (let step = 0; step < cells.length; step++) {
    i += delta;
    if (i < 0 || i >= cells.length) return from;
    if (cells[i] != null) return i;
  }
  return from;
}

/** Arrow-key grid navigation, Enter/Space select, Escape close for calendar popovers. */
export function useCalendarKeyboard({
  open,
  cells,
  selectedDay,
  todayDay,
  onSelectDay,
  onClose,
  triggerRef,
}: UseCalendarKeyboardArgs) {
  const [focusIndex, setFocusIndex] = useState(-1);

  useEffect(() => {
    if (!open) {
      setFocusIndex(-1);
      return;
    }
    setFocusIndex(initialFocusIndex(cells, selectedDay, todayDay));
  }, [open, cells, selectedDay, todayDay]);

  useEffect(() => {
    if (!open || focusIndex < 0) return;
    const el = document.querySelector<HTMLElement>(
      `[data-calendar-index="${focusIndex}"]`,
    );
    el?.focus();
  }, [open, focusIndex]);

  const handleGridKeyDown = useCallback(
    (e: ReactKeyboardEvent) => {
      if (!open) return;

      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
        triggerRef.current?.focus();
        return;
      }

      if (focusIndex < 0) return;

      if (e.key === "ArrowLeft") {
        e.preventDefault();
        setFocusIndex((i) => moveFocusIndex(cells, i, -1));
        return;
      }
      if (e.key === "ArrowRight") {
        e.preventDefault();
        setFocusIndex((i) => moveFocusIndex(cells, i, 1));
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setFocusIndex((i) => moveFocusIndex(cells, i, -7));
        return;
      }
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setFocusIndex((i) => moveFocusIndex(cells, i, 7));
        return;
      }
      if (e.key === "Enter" || e.key === " ") {
        const day = cells[focusIndex];
        if (day == null) return;
        e.preventDefault();
        onSelectDay(day);
      }
    },
    [open, focusIndex, cells, onSelectDay, onClose, triggerRef],
  );

  const getDayButtonProps = useCallback(
    (cellIndex: number, day: number) => ({
      tabIndex: cellIndex === focusIndex ? 0 : -1,
      "data-calendar-index": cellIndex,
      "aria-selected": selectedDay === day,
      onKeyDown: handleGridKeyDown,
      onFocus: () => setFocusIndex(cellIndex),
    }),
    [focusIndex, selectedDay, handleGridKeyDown],
  );

  return { focusIndex, getDayButtonProps, handleGridKeyDown };
}
