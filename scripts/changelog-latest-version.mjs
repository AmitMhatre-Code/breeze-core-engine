#!/usr/bin/env node
/**
 * Print the newest changelog version (first entry in changelogReleases).
 * Usage: node scripts/changelog-latest-version.mjs [path/to/changelog.ts]
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const defaultPath = resolve(
  import.meta.dirname,
  "../frontend/src/lib/changelog.ts",
);
const changelogPath = process.argv[2] ? resolve(process.argv[2]) : defaultPath;

const text = readFileSync(changelogPath, "utf8");
const marker = "export const changelogReleases";
const idx = text.indexOf(marker);
if (idx < 0) {
  console.error(`changelog-latest-version: missing ${marker} in ${changelogPath}`);
  process.exit(1);
}
const slice = text.slice(idx);
const match = slice.match(/version:\s*["']([^"']+)["']/);
if (!match?.[1]) {
  console.error(`changelog-latest-version: no version field in ${changelogPath}`);
  process.exit(1);
}
process.stdout.write(match[1]);
