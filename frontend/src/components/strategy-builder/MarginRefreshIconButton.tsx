"use client";

export function MarginRefreshIconButton({
  onClick,
  disabled,
  label,
  title: titleProp,
}: {
  onClick: () => void;
  disabled?: boolean;
  label: string;
  title?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label={label}
      title={titleProp ?? label}
      className="inline-flex h-7 w-7 items-center justify-center rounded-md text-muted hover:bg-panel2 hover:text-foreground disabled:cursor-not-allowed disabled:text-faint disabled:hover:bg-transparent"
    >
      <svg
        className="h-4 w-4"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden
      >
        <path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8" />
        <path d="M21 3v5h-5" />
        <path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16" />
        <path d="M21 21v-5h-5" />
      </svg>
    </button>
  );
}
