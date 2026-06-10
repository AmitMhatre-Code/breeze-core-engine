import { apiClient } from "@/lib/api-client";

export type AuthSession = {
  authenticated: boolean;
  user_id?: string | null;
};

export function fetchAuthSession(): Promise<AuthSession> {
  return apiClient.get<AuthSession>("/auth/session", { sessionPolicy: "passive" });
}
