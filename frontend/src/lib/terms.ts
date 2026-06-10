import { apiClient } from "@/lib/api-client";

export type TermsStatus = {
  needs_acceptance: boolean;
  current_version: number | null;
  accepted_version: number | null;
  accepted_at: string | null;
  content_markdown?: string | null;
  portal_configured?: boolean;
};

export type TermsDocument = {
  version: number;
  content_markdown: string;
  effective_date: string;
};

export function fetchTermsStatus(): Promise<TermsStatus> {
  return apiClient.get<TermsStatus>("/api/terms/status", { sessionPolicy: "passive" });
}

export function fetchTermsCurrent(): Promise<TermsDocument> {
  return apiClient.get<TermsDocument>("/api/terms/current");
}

export function acceptTerms(termsVersion: number): Promise<{ ok: boolean }> {
  return apiClient.post<{ ok: boolean }, { terms_version: number }>("/api/terms/accept", {
    terms_version: termsVersion,
  });
}
