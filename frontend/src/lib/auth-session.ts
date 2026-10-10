import { apiClient } from "@/lib/api-client";

export type AuthSession = {
  authenticated: boolean;
  user_id?: string | null;
  /** The access token's issue time: identifies the sign-in across tabs. */
  signed_in_at?: number | null;
};

export function fetchAuthSession(): Promise<AuthSession> {
  return apiClient.get<AuthSession>("/auth/session", { sessionPolicy: "passive" });
}
