/** Semver-like compare for core-engine app versions (supports 1.4.2-a). */

export type ParsedAppVersion = {
  major: number;
  minor: number;
  patch: number;
  prerelease?: string;
};

/** Canonical semver display (1.4.2-a); accepts legacy dot suffix (1.4.2.a). */
export function formatAppVersionForDisplay(
  raw: string | null | undefined,
): string {
  const s = (raw || "").trim();
  if (!s) return s;
  const dotted = s.match(/^(\d+)\.(\d+)\.(\d+)\.([a-zA-Z0-9]+)$/);
  if (dotted) {
    return `${dotted[1]}.${dotted[2]}.${dotted[3]}-${dotted[4]}`;
  }
  return s;
}

export function formatAppVersionLabel(
  raw: string | null | undefined,
): string {
  const display = formatAppVersionForDisplay(raw);
  return display ? `v${display}` : "";
}

export function normalizeAppVersionForCompare(
  raw: string | null | undefined,
): string | null {
  const s = (raw || "").trim();
  if (!s || s.toLowerCase() === "unknown") return null;
  if (s.includes("/") && s.includes(":")) {
    const tag = s.split(":").pop()?.trim();
    if (!tag) return null;
    return normalizeAppVersionForCompare(tag);
  }
  if (!/^[0-9A-Za-z][0-9A-Za-z._-]*$/.test(s)) return null;
  const dotted = s.match(/^(\d+)\.(\d+)\.(\d+)\.([a-zA-Z0-9]+)$/);
  if (dotted) {
    return `${dotted[1]}.${dotted[2]}.${dotted[3]}-${dotted[4]}`;
  }
  const main = s.split("-", 1)[0];
  const parts = main.split(".");
  if (parts.length !== 3 || !parts.every((p) => /^\d+$/.test(p))) return null;
  return s;
}

export function parseAppVersion(
  raw: string | null | undefined,
): ParsedAppVersion | null {
  const norm = normalizeAppVersionForCompare(raw);
  if (!norm) return null;
  const [main, prerelease = ""] = norm.includes("-")
    ? norm.split("-", 2)
    : [norm, ""];
  const parts = main.split(".");
  if (parts.length !== 3) return null;
  const major = Number(parts[0]);
  const minor = Number(parts[1]);
  const patch = Number(parts[2]);
  if (!Number.isFinite(major) || !Number.isFinite(minor) || !Number.isFinite(patch)) {
    return null;
  }
  return {
    major,
    minor,
    patch,
    ...(prerelease ? { prerelease: prerelease.toLowerCase() } : {}),
  };
}

function versionKey(
  raw: string | null | undefined,
): [number, number, number, string] | null {
  const parsed = parseAppVersion(raw);
  if (!parsed) return null;
  return [parsed.major, parsed.minor, parsed.patch, parsed.prerelease ?? ""];
}

export function compareAppVersions(
  running: string | null | undefined,
  latest: string | null | undefined,
): number {
  const a = versionKey(running);
  const b = versionKey(latest);
  if (!b) return 0;
  if (!a) return -1;
  if (a < b) return -1;
  if (a > b) return 1;
  return 0;
}

export function isAppVersionBehind(
  running: string | null | undefined,
  latest: string | null | undefined,
): boolean {
  return compareAppVersions(running, latest) < 0;
}
