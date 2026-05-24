"use client";

import { usePathname } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { apiClient } from "@/lib/api-client";
import {
  isTradingReadOnly,
  type DeploymentLicenseStatus,
} from "@/lib/deployment-license";
import type { DeploymentLicenseStatusResponse } from "@/lib/deployment-license-status";
import { shouldFetchLicenseHomeData } from "@/lib/public-auth-routes";

export function useDeploymentLicense() {
  const pathname = usePathname();
  const enabled = shouldFetchLicenseHomeData(pathname);

  return useQuery({
    queryKey: ["deployment", "license-status"],
    queryFn: () =>
      apiClient.get<DeploymentLicenseStatusResponse>(
        "/deployment/license-status",
        { sessionPolicy: "passive" },
      ),
    staleTime: 30_000,
    enabled,
    retry: false,
    placeholderData: (prev) => prev,
    refetchInterval: (query) => {
      const status = query.state.data?.deployment_license_status;
      return status === "expired" || status === "revoked" || status === "unlicensed"
        ? 60_000
        : false;
    },
  });
}

export function licenseStatusFromQuery(
  data: DeploymentLicenseStatusResponse | undefined,
): DeploymentLicenseStatus | null {
  return data?.deployment_license_status ?? null;
}

export function tradingReadOnlyFromLicense(
  data: DeploymentLicenseStatusResponse | undefined,
): boolean {
  return (
    data?.deployment_license_read_only === true ||
    isTradingReadOnly(data?.deployment_license_status)
  );
}
