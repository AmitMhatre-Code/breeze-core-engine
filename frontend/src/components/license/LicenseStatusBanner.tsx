import {
  licenseBannerMessage,
  LICENSE_CONSOLE_URL,
  shouldShowLicenseBanner,
  type DeploymentLicenseStatus,
} from "@/lib/deployment-license";

export function LicenseStatusBanner({
  status,
  contactSalesMailto,
}: {
  status: DeploymentLicenseStatus | null | undefined;
  contactSalesMailto?: string | null;
}) {
  if (!shouldShowLicenseBanner(status)) return null;

  const message = licenseBannerMessage(status);
  if (!message) return null;

  const isExpired = status === "expired";
  const isWarningOnly = status === "expired" || status === "pending_activation";
  const linkInMessage = message.includes("breeze-ui.com");
  const [before, after] = linkInMessage ? message.split("breeze-ui.com") : [message, ""];

  return (
    <div
      role="status"
      className={[
        "border-b px-4 py-2 text-center text-sm",
        isWarningOnly
          ? "border-amber-500/60 bg-amber-500/10 text-amber-950 dark:text-amber-100"
          : "border-red-500/60 bg-red-500/10 text-red-950 dark:text-red-100",
      ].join(" ")}
    >
      <div className="flex flex-wrap items-center justify-center gap-x-2 gap-y-1">
        <span>
          {before}
          {linkInMessage ? (
            <a
              href={LICENSE_CONSOLE_URL}
              target="_blank"
              rel="noopener noreferrer"
              className={[
                "font-medium underline underline-offset-2",
                isWarningOnly
                  ? "text-amber-900 hover:text-amber-950 dark:text-amber-50 dark:hover:text-white"
                  : "text-red-900 hover:text-red-950 dark:text-red-50 dark:hover:text-white",
              ].join(" ")}
            >
              breeze-ui.com
            </a>
          ) : null}
          {after}
        </span>
        {contactSalesMailto ? (
          <>
            <span className="hidden sm:inline" aria-hidden="true">
              ·
            </span>
            <a
              href={contactSalesMailto}
              className={[
                "font-semibold underline underline-offset-2",
                isWarningOnly
                  ? "text-amber-900 hover:text-amber-950 dark:text-amber-50 dark:hover:text-white"
                  : "text-red-900 hover:text-red-950 dark:text-red-50 dark:hover:text-white",
              ].join(" ")}
            >
              Contact Sales
            </a>
          </>
        ) : null}
      </div>
    </div>
  );
}
