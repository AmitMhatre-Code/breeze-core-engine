/**
 * App routes where the user is often unauthenticated. Do not probe /home/data for
 * license status or treat 401s as "session expired" (would eject ICICI challenge / register).
 */
const PUBLIC_UNAUTHENTICATED_PATH_PREFIXES = [
  "/login",
  "/challenge",
  "/logout",
  "/register",
] as const;

export function isPublicUnauthenticatedPath(
  pathname: string | null | undefined,
): boolean {
  if (!pathname) return false;
  if (pathname === "/") return true;
  return PUBLIC_UNAUTHENTICATED_PATH_PREFIXES.some(
    (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`),
  );
}

export function shouldFetchLicenseHomeData(
  pathname: string | null | undefined,
): boolean {
  return !isPublicUnauthenticatedPath(pathname);
}
