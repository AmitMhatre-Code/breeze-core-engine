/**
 * Deployment license status from portal heartbeat cache.
 * UI polling uses GET /deployment/license-status (passive session; does not affect JWT).
 * GET /home/data may still include these fields for backward compatibility.
 */

export type DeploymentLicenseStatus =
  | "active"
  | "expired"
  | "revoked"
  | "unlicensed"
  | "pending_activation"
  | "trial_denied";

export const LICENSE_CONSOLE_URL = "https://breeze-ui.com";

export const LICENSE_EXPIRED_BANNER =
  "License expired — sign in at breeze-ui.com and follow the instructions in the license console to extend your license.";

export const LICENSE_REVOKED_BANNER =
  "Read-only mode — you cannot define strategies or execute trades. Sign in at breeze-ui.com and follow the instructions for your license to reactivate this application.";

export const LICENSE_UNLICENSED_BANNER =
  "Read-only mode — this deployment has no valid license. Sign in at breeze-ui.com to obtain a deployment license and configure it on this instance.";

export const LICENSE_PENDING_ACTIVATION_BANNER =
  "Complete ICICI Direct login on this instance to start your 14-day trial. Trading stays read-only until activation succeeds.";

export const LICENSE_TRIAL_DENIED_BANNER =
  "Trial already used for this ICICI Direct User ID — contact sales@breeze-ui.com for a paid license.";

export function isTradingReadOnly(
  status: DeploymentLicenseStatus | null | undefined,
): boolean {
  return (
    status === "revoked" ||
    status === "unlicensed" ||
    status === "pending_activation" ||
    status === "trial_denied"
  );
}

export function shouldShowLicenseBanner(
  status: DeploymentLicenseStatus | null | undefined,
): status is
  | "expired"
  | "revoked"
  | "unlicensed"
  | "pending_activation"
  | "trial_denied" {
  return (
    status === "expired" ||
    status === "revoked" ||
    status === "unlicensed" ||
    status === "pending_activation" ||
    status === "trial_denied"
  );
}

export function licenseBannerMessage(
  status: DeploymentLicenseStatus | null | undefined,
): string | null {
  if (status === "expired") return LICENSE_EXPIRED_BANNER;
  if (status === "revoked") return LICENSE_REVOKED_BANNER;
  if (status === "unlicensed") return LICENSE_UNLICENSED_BANNER;
  if (status === "pending_activation") return LICENSE_PENDING_ACTIVATION_BANNER;
  if (status === "trial_denied") return LICENSE_TRIAL_DENIED_BANNER;
  return null;
}
