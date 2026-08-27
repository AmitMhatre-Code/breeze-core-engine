import { HelpLink } from "@/components/help/HelpLink";

/**
 * Persistent (non-dismissable) banner for when the ICICI 5000-calls/day cap is fully
 * exhausted -- distinct from ApiUsageWarningDialog, which only fires while *approaching*
 * the cap and stops firing once it's reached (see get_usage_warning on the backend).
 */
export function ApiLimitExhaustedBanner({ blocked }: { blocked: boolean }) {
  if (!blocked) return null;

  return (
    <div
      role="status"
      className="border-b border-red-500/60 bg-red-500/10 py-2 text-center text-sm ps-[max(1rem,env(safe-area-inset-left))] pe-[max(1rem,env(safe-area-inset-right))] text-red-950 dark:text-red-100"
    >
      <div className="flex flex-wrap items-center justify-center gap-x-2 gap-y-1">
        <span>
          You have hit ICICI&apos;s daily limit of 5,000 API calls. Broker features
          (positions, margin, quotes, orders) will not work until the limit resets at
          midnight IST.
        </span>
        <HelpLink
          topicId="api-usage-limits"
          className="font-medium underline underline-offset-2 text-red-900 hover:text-red-950 dark:text-red-50 dark:hover:text-white"
        >
          Learn more
        </HelpLink>
      </div>
    </div>
  );
}
