const KEY = "breeze_login_disclosure_ack";

function ackValue(userId: string, version: number): string {
  return `${userId.trim().toUpperCase()}:${version}`;
}

export function hasSessionAck(userId: string, version: number): boolean {
  if (typeof sessionStorage === "undefined") return false;
  const uid = userId.trim().toUpperCase();
  if (!uid || version < 1) return false;
  return sessionStorage.getItem(KEY) === ackValue(uid, version);
}

export function setSessionAck(userId: string, version: number): void {
  if (typeof sessionStorage === "undefined") return;
  const uid = userId.trim().toUpperCase();
  if (!uid || version < 1) return;
  sessionStorage.setItem(KEY, ackValue(uid, version));
}

export function clearSessionAck(): void {
  if (typeof sessionStorage === "undefined") return;
  sessionStorage.removeItem(KEY);
}
