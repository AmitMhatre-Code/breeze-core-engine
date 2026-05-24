import {
  licenseBannerMessage,
  LICENSE_CONSOLE_URL,
  shouldShowLicenseBanner,
  type DeploymentLicenseStatus,
} from "@/lib/deployment-license";

export function LicenseStatusBanner({
  status,
}: {
  status: DeploymentLicenseStatus | null | undefined;
}) {
  if (!shouldShowLicenseBanner(status)) return null;

  const message = licenseBannerMessage(status);
  if (!message) return null;

  const isExpired = status === "expired";
  const [before, after] = message.split("breeze-ui.com");

  return (
    <div
      role="status"
      className={[
        "border-b px-4 py-2 text-center text-sm",
        isExpired
          ? "border-amber-500/60 bg-amber-500/10 text-amber-950 dark:text-amber-100"
          : "border-red-500/60 bg-red-500/10 text-red-950 dark:text-red-100",
      ].join(" ")}
    >
      {before}
      <a
        href={LICENSE_CONSOLE_URL}
        target="_blank"
        rel="noopener noreferrer"
        className={[
          "font-medium underline underline-offset-2",
          isExpired
            ? "text-amber-900 hover:text-amber-950 dark:text-amber-50 dark:hover:text-white"
            : "text-red-900 hover:text-red-950 dark:text-red-50 dark:hover:text-white",
        ].join(" ")}
      >
        breeze-ui.com
      </a>
      {after}
    </div>
  );
}
