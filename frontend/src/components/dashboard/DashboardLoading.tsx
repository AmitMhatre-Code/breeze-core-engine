type SkeletonProps = {
  className?: string;
};

export function DashboardMetricSkeleton({ className }: SkeletonProps) {
  return (
    <div
      className={["app-skeleton h-7 w-16 rounded-sm border-0", className]
        .filter(Boolean)
        .join(" ")}
      aria-hidden
    />
  );
}

export function DashboardChartSkeleton({ className }: SkeletonProps) {
  return (
    <div
      className={["app-skeleton min-h-[148px] w-full rounded-md border-0", className]
        .filter(Boolean)
        .join(" ")}
      aria-hidden
    />
  );
}

export function DashboardTrendSkeleton() {
  return (
    <span
      className="app-skeleton inline-block h-5 w-14 rounded-full border-0"
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
