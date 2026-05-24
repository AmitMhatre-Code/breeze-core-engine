/**
 * Deployment license status from portal heartbeat cache.
 * UI polling uses GET /deployment/license-status (passive session; does not affect JWT).
 * GET /home/data may still include these fields for backward compatibility.
 */

export type DeploymentLicenseStatus = "active" | "expired" | "revoked";

export const LICENSE_CONSOLE_URL = "https://breeze-ui.com";

export const LICENSE_EXPIRED_BANNER =
  "License expired — sign in at breeze-ui.com and follow the instructions in the license console to extend your license.";

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
