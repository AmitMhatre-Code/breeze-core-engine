import type { ReactNode } from "react";

export function SettingsScreenHeader({
  icon,
  title,
  description,
  danger = false,
}: {
  icon: ReactNode;
  title: string;
  description?: ReactNode;
  danger?: boolean;
}) {
  return (
    <div className="mb-5 flex items-start gap-3">
      <span
        className={[
          "flex size-[38px] shrink-0 items-center justify-center rounded-[10px]",
          danger ? "bg-down-tint text-down" : "bg-accent-tint text-accent-strong",
        ].join(" ")}
        aria-hidden
      >
        {icon}
      </span>
      <div className="min-w-0 pt-0.5">
        <h2 className={["text-subtitle font-bold", danger ? "text-down" : "text-foreground"].join(" ")}>
          {title}
        </h2>
        {description ? (
          <p className="mt-0.5 text-xs leading-relaxed text-muted">{description}</p>
        ) : null}
      </div>
    </div>
  );
}
