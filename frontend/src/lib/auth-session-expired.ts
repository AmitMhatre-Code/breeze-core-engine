/**
 * When the backend returns 401 for authenticated routes, clear server session and
 * send the user to login with a clear re-authentication message.
 */

export const LOGIN_REAUTH_REASON_QUERY = "session";

let sessionExpiredRedirectPending = false;

export function isSessionExpiredRedirectPending(): boolean {
  return sessionExpiredRedirectPending;
}

const UNAUTH_REDIRECT_PATH_PREFIXES = ["/api/register"];

const UNAUTH_REDIRECT_EXACT_PATHS = new Set([
  "/auth/direct-login",
  // Session probe: 401 means "not signed in yet", not "session expired".
  "/auth/session",
]);

/** 401 cases where the user is not in an “logged in but stale” state. */
function isBenignUnauthorizedMessage(message: string): boolean {
  const m = message.toLowerCase();
  return (
    m.includes("invalid credentials") ||
    m.includes("sign in with google first")
  );
}

function shouldAutoLogoutOn401(apiPath: string, message: string): boolean {
  const pathOnly = apiPath.split("?")[0];
  if (UNAUTH_REDIRECT_EXACT_PATHS.has(pathOnly)) return false;
  if (UNAUTH_REDIRECT_PATH_PREFIXES.some((p) => pathOnly === p || pathOnly.startsWith(`${p}/`))) {
    return false;
  }
  if (isBenignUnauthorizedMessage(message)) return false;
  return true;
}

/**
 * Clears cookies via backend logout, then navigates to login with reason query.
 * @returns true if this request triggered (or is already triggering) that flow.
 */
export async function handleUnauthorizedApiResponse(
  apiPath: string,
  status: number,
  message: string,
): Promise<boolean> {
  if (typeof window === "undefined" || status !== 401) return false;
  if (!shouldAutoLogoutOn401(apiPath, message)) return false;
  if (window.location.pathname === "/login") return false;

  if (sessionExpiredRedirectPending) return true;
  sessionExpiredRedirectPending = true;

  try {
    await fetch("/auth/logout", {
      method: "POST",
      credentials: "include",
    });
  } catch {
    // Still navigate to login; cookies may already be invalid.
  }

  const next = new URL("/login", window.location.origin);
  next.searchParams.set("reason", LOGIN_REAUTH_REASON_QUERY);
  window.location.replace(next.toString());
  return true;
}
