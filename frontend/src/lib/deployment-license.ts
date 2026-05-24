/** Deployment license status from GET /home/data (portal heartbeat cache). */

export type DeploymentLicenseStatus = "active" | "expired" | "revoked";

export const LICENSE_CONSOLE_URL = "https://breeze-ui.com";

export const LICENSE_EXPIRED_BANNER =
  "License expired — sign in at breeze-ui.com and follow the instructions in the license console to extend your license. All features remain available until your license is renewed.";

export const LICENSE_REVOKED_BANNER =
  "Read-only mode — you cannot define strategies or execute trades. Sign in at breeze-ui.com and follow the instructions for your license to reactivate this application.";

export function isTradingReadOnly(
  status: DeploymentLicenseStatus | null | undefined,
): boolean {
  return status === "revoked";
}

export function shouldShowLicenseBanner(
  status: DeploymentLicenseStatus | null | undefined,
): status is "expired" | "revoked" {
  return status === "expired" || status === "revoked";
}

export function licenseBannerMessage(
  status: DeploymentLicenseStatus | null | undefined,
): string | null {
  if (status === "expired") return LICENSE_EXPIRED_BANNER;
  if (status === "revoked") return LICENSE_REVOKED_BANNER;
  return null;
}
