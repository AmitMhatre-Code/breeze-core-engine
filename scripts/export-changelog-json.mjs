#!/usr/bin/env node
/**
 * Export changelog releases to JSON (for vendoring into saas-portal).
 * Usage: node scripts/export-changelog-json.mjs [changelog.ts] [output.json]
 */
import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

const defaultChangelog = resolve(
  import.meta.dirname,
  "../frontend/src/lib/changelog.ts",
);
const changelogPath = process.argv[2] ? resolve(process.argv[2]) : defaultChangelog;
const outPath =
  process.argv[3] ||
  resolve(import.meta.dirname, "../../breeze-saas-portal/backend/data/core_engine_changelog.json");

const text = readFileSync(changelogPath, "utf8");
const marker = "export const changelogReleases";
const start = text.indexOf(marker);
if (start < 0) {
  console.error("export-changelog-json: missing changelogReleases");
  process.exit(1);
}
const arrStart = text.indexOf("[", start);
const arrEnd = text.indexOf("];", arrStart);
if (arrStart < 0 || arrEnd < 0) {
  console.error("export-changelog-json: could not parse array");
  process.exit(1);
}
const arrText = text.slice(arrStart, arrEnd + 1);
// eslint-disable-next-line no-new-func
const releases = Function(`"use strict"; return (${arrText});`)();
if (!Array.isArray(releases) || releases.length === 0) {
  console.error("export-changelog-json: empty releases");
  process.exit(1);
}
writeFileSync(outPath, JSON.stringify({ releases }, null, 2) + "\n");
console.error(`Wrote ${releases.length} releases to ${outPath}`);
