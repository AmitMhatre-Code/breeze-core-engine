/**
 * Base URL for browser `fetch` calls. OAuth cookies are set on whatever host the user used
 * for `/auth/*`; requests must use that same origin or cookies are not sent (e.g. localhost vs
 * 127.0.0.1, or a custom port mapping). Server-side fallback is for non-browser contexts.
 */
export function getBackendBaseUrl(): string {
  const env = process.env.NEXT_PUBLIC_BACKEND_URL;
  if (typeof env === "string" && env.trim() !== "") {
    return env.replace(/\/$/, "");
  }
  if (typeof window !== "undefined") {
    return window.location.origin;
  }
  return "http://localhost:3000";
}

