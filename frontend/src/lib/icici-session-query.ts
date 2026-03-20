/** Query keys ICICI may use when redirecting back to the app (mirror backend home._ICICI_SESSION_QUERY_KEYS). */
const ICICI_SESSION_QUERY_KEYS = [
  "apisession",
  "session_token",
  "API_Session",
  "api_session",
  "sessionToken",
  "SessionToken",
  "token",
] as const;

/** First non-empty session token from URL search params, if any. */
export function getIciciSessionFromSearchParams(
  sp: URLSearchParams,
): string | null {
  for (const key of ICICI_SESSION_QUERY_KEYS) {
    const v = sp.get(key);
    const t = v?.trim();
    if (t && t.toLowerCase() !== "none") return t;
  }
  return null;
}
