"use client";

import { useEffect, useRef } from "react";

type CheckboxProps = {
  checked: boolean;
  indeterminate?: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
  className?: string;
  "aria-label"?: string;
};

/** Flat 16px square checkbox — Terminal Pro design (accent fill + ink check, no native browser chrome). */
export function Checkbox({
  checked,
  indeterminate = false,
  onChange,
  disabled,
  className,
  ...rest
}: CheckboxProps) {
  const ref = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (ref.current) ref.current.indeterminate = indeterminate;
  }, [indeterminate]);

  return (
    <span
      className={[
        "relative inline-flex size-4 shrink-0 items-center justify-center",
        className ?? "",
      ].join(" ")}
    >
      <input
        ref={ref}
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
        className="peer size-4 shrink-0 cursor-pointer appearance-none rounded border border-border bg-panel2 transition checked:border-accent-strong checked:bg-accent-strong indeterminate:border-accent-strong indeterminate:bg-accent-strong focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/30 disabled:cursor-not-allowed disabled:opacity-50"
        {...rest}
      />
      <svg
        className="pointer-events-none absolute inset-0 m-auto text-accent-ink opacity-0 peer-checked:opacity-100"
        width="10"
        height="10"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="3"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden
      >
        <path d="M20 6 9 17l-5-5" />
      </svg>
      <span
        className="pointer-events-none absolute h-[2px] w-2 scale-0 rounded-full bg-accent-ink transition-transform peer-indeterminate:scale-100"
        aria-hidden
      />
    </span>
  );
}
