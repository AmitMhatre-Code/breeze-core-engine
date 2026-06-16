type SkeletonProps = {
  className?: string;
};

export function DashboardMetricSkeleton({ className }: SkeletonProps) {
  return (
    <div
      className={[
        "app-card-muted h-7 w-16 animate-pulse rounded-sm",
        className,
      ]
        .filter(Boolean)
        .join(" ")}
      aria-hidden
    />
  );
}

export function DashboardChartSkeleton({ className }: SkeletonProps) {
  return (
    <div
      className={[
        "app-card-muted min-h-[148px] w-full animate-pulse rounded-md",
        className,
      ]
        .filter(Boolean)
        .join(" ")}
      aria-hidden
    />
  );
}

export function DashboardTrendSkeleton() {
  return (
    <span
      className="inline-block h-5 w-14 animate-pulse rounded-full bg-zinc-200 dark:bg-zinc-800"
      aria-hidden
    />
  );
}

export function DashboardSectionStatus({ children }: { children: string }) {
  return (
    <p className="px-2 text-xs app-text-muted" role="status">
      {children}
    </p>
  );
}
