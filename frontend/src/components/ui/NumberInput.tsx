"use client";

import { createContext, useContext, useEffect, useId, useRef, useState } from "react";

/** Set by a form that wants to block its own Save while any descendant field is empty or
 *  out of range. A `NumberInput` with a `validityKey` reports through this automatically. */
export const FieldValidityContext = createContext<
  ((key: string, valid: boolean) => void) | null
>(null);

type NumberInputProps = {
  value: number;
  onChange: (value: number) => void;
  /** Fired whenever the field's validity flips. Empty or out-of-range counts as invalid —
   *  the caller keeps Save/Execute disabled until every field it owns is valid again. */
  onValidityChange?: (valid: boolean) => void;
  /** Identifier under which this field's validity is reported to `FieldValidityContext`. */
  validityKey?: string;
  min?: number;
  max?: number;
  step?: number;
  disabled?: boolean;
  placeholder?: string;
  className?: string;
  "aria-label"?: string;
};

function parse(text: string, min?: number, max?: number): number | null {
  const trimmed = text.trim();
  if (trimmed === "") return null;
  const n = Number(trimmed);
  if (!Number.isFinite(n)) return null;
  if (min != null && n < min) return null;
  if (max != null && n > max) return null;
  return n;
}

/** A numeric field that never snaps to 0 when you clear it.
 *
 *  A raw `<input type="number">` with `onChange={e => onChange(Number(e.target.value))}`
 *  turns an empty box into `0`, so deleting a value to retype it strands a leading zero.
 *  This keeps its own text buffer: an empty or half-typed value simply doesn't commit, and
 *  is reported as invalid so the surrounding form can block its Save until it is fixed.
 */
export function NumberInput({
  value,
  onChange,
  onValidityChange,
  validityKey,
  min,
  max,
  step = 1,
  disabled,
  placeholder,
  className,
  ...rest
}: NumberInputProps) {
  const [text, setText] = useState(() => String(value));
  const focused = useRef(false);
  const id = useId();
  const reportToGroup = useContext(FieldValidityContext);
  const groupKey = validityKey ?? id;

  // Adopt an externally-changed value (server normalisation, a reprice) only while the
  // user is not mid-edit, so their keystrokes are never overwritten.
  useEffect(() => {
    if (!focused.current) setText(String(value));
  }, [value]);

  const parsed = parse(text, min, max);
  const invalid = parsed === null;

  // Report validity to the parent, but never let a disabled field hold Save hostage.
  useEffect(() => {
    const valid = disabled ? true : !invalid;
    onValidityChange?.(valid);
    reportToGroup?.(groupKey, valid);
    return () => {
      onValidityChange?.(true);
      reportToGroup?.(groupKey, true);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [invalid, disabled, groupKey]);

  return (
    <input
      id={id}
      type="number"
      inputMode="decimal"
      value={text}
      min={min}
      max={max}
      step={step}
      disabled={disabled}
      placeholder={placeholder}
      aria-invalid={!disabled && invalid}
      onFocus={() => {
        focused.current = true;
      }}
      onBlur={() => {
        focused.current = false;
        if (parsed !== null) setText(String(parsed));
      }}
      onChange={(e) => {
        setText(e.target.value);
        const next = parse(e.target.value, min, max);
        if (next !== null) onChange(next);
      }}
      className={[
        className ?? "app-input",
        // Utilities sit in a later layer than the `app-input` component class, so these
        // win over its border colour without needing an important modifier.
        !disabled && invalid ? "border-down text-down focus:border-down" : "",
      ].join(" ")}
      {...rest}
    />
  );
}
