import { apiClient } from "@/lib/api-client";

export type TermsDocument = {
  version: number;
  content_markdown: string;
  effective_date: string;
};

export function fetchTermsCurrent(): Promise<TermsDocument> {
  return apiClient.get<TermsDocument>("/api/terms/current");
}
