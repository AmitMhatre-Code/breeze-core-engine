import { clearPreloadedLoginDisclosure } from "@/lib/login-disclosure-preload";

/**
 * The ack is per sign-in, not per tab: it lives in localStorage (shared by every tab)
 * and carries the sign-in's `signed_in_at` from /auth/session, so a new tab of the same
 * sign-in is not asked again while the next sign-in always is. The pending flag stays in
 * sessionStorage — it belongs to the one tab that just came back from ICICI login.
 */
export const ACK_KEY = "breeze_login_disclosure_ack";
const PENDING_KEY = "breeze_login_disclosure_pending";

function ackStore(): Storage | null {
  return typeof localStorage === "undefined" ? null : localStorage;
}

function signInPart(signedInAt: number | null | undefined): string {
  return signedInAt == null ? "" : String(signedInAt);
}

function ackValue(userId: string, version: number, signedInAt: number | null | undefined): string {
  return `${userId.trim().toUpperCase()}:${version}:${signInPart(signedInAt)}`;
}

export function hasSessionAck(
  userId: string,
  version: number,
  signedInAt: number | null | undefined,
): boolean {
  const store = ackStore();
  if (!store) return false;
  const uid = userId.trim().toUpperCase();
  if (!uid || version < 1) return false;
  return store.getItem(ACK_KEY) === ackValue(uid, version, signedInAt);
}

export function getStoredSessionAckVersion(
  userId: string,
  signedInAt: number | null | undefined,
): number | null {
  const store = ackStore();
  if (!store) return null;
  const uid = userId.trim().toUpperCase();
  if (!uid) return null;

  const stored = store.getItem(ACK_KEY);
  if (!stored) return null;

  const [storedUid, storedVersion, storedSignIn, ...rest] = stored.split(":");
  if (rest.length || storedSignIn === undefined) return null;
  if (storedUid.trim().toUpperCase() !== uid) return null;
  if (storedSignIn !== signInPart(signedInAt)) return null;

  const version = Number.parseInt(storedVersion, 10);
  return Number.isFinite(version) && version >= 1 ? version : null;
}

export function hasStoredSessionAckForUser(
  userId: string,
  signedInAt: number | null | undefined,
): boolean {
  return getStoredSessionAckVersion(userId, signedInAt) != null;
}

export function setSessionAck(
  userId: string,
  version: number,
  signedInAt: number | null | undefined,
): void {
  const store = ackStore();
  if (!store) return;
  const uid = userId.trim().toUpperCase();
  if (!uid || version < 1) return;
  store.setItem(ACK_KEY, ackValue(uid, version, signedInAt));
}

export function markDisclosurePending(): void {
  if (typeof sessionStorage === "undefined") return;
  sessionStorage.setItem(PENDING_KEY, "1");
}

export function isDisclosurePending(): boolean {
  if (typeof sessionStorage === "undefined") return false;
  return sessionStorage.getItem(PENDING_KEY) === "1";
}

export function clearDisclosurePending(): void {
  if (typeof sessionStorage === "undefined") return;
  sessionStorage.removeItem(PENDING_KEY);
}

export function clearSessionAck(): void {
  ackStore()?.removeItem(ACK_KEY);
  if (typeof sessionStorage !== "undefined") {
    // Acks written before they moved to localStorage.
    sessionStorage.removeItem(ACK_KEY);
    sessionStorage.removeItem(PENDING_KEY);
  }
  clearPreloadedLoginDisclosure();
}
