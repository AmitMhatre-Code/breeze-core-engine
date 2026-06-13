import { apiClient } from "@/lib/api-client";

export type LoginDisclosureDocument = {
  version: number;
  content_markdown: string;
  effective_date: string;
  portal_configured?: boolean;
};

export function fetchLoginDisclosurePublicCurrent(): Promise<LoginDisclosureDocument> {
  return apiClient.get<LoginDisclosureDocument>("/api/login-disclosure/public/current", {
    sessionPolicy: "passive",
  });
}

export function fetchLoginDisclosureCurrent(): Promise<LoginDisclosureDocument> {
  return apiClient.get<LoginDisclosureDocument>("/api/login-disclosure/current", {
    sessionPolicy: "passive",
  });
}

export function acceptLoginDisclosure(disclosureVersion: number): Promise<{ ok: boolean }> {
  return apiClient.post<{ ok: boolean }, { disclosure_version: number }>(
    "/api/login-disclosure/accept",
    { disclosure_version: disclosureVersion },
  );
}
