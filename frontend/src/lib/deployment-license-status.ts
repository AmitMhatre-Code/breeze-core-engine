/** Response from GET /deployment/license-status (portal heartbeat cache). */

import type { DeploymentLicenseStatus } from "@/lib/deployment-license";

export type DeploymentLicenseContactSales = {
  license_key?: string | null;
  public_ip?: string | null;
  deployment_origin?: string | null;
  app_version?: string | null;
};

export type DeploymentLicenseStatusResponse = {
  deployment_license_status?: DeploymentLicenseStatus | null;
  deployment_license_read_only?: boolean;
  contact_sales?: DeploymentLicenseContactSales | null;
};
