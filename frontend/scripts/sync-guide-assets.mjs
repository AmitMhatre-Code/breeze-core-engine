#!/usr/bin/env node
// Copies the user guide's screenshots (repo-root guide/user/images/{dark,light}) into
// public/guide-assets/ so Next serves them as static files. Runs before `next dev` and
// `next build` (predev/prebuild). The Markdown itself is read at build time by
// src/lib/guide/content.ts; only images need to be in public/.
//
// Reads guide/user/ ONLY — guide/technical/ is repo-only and must never reach the app.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const frontendDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const guideUserDir = path.resolve(frontendDir, "..", "guide", "user");
const src = path.join(guideUserDir, "images");
const dest = path.join(frontendDir, "public", "guide-assets");

if (!fs.existsSync(path.join(guideUserDir, "guide.json"))) {
  console.error(
    `sync-guide-assets: ${guideUserDir}/guide.json not found. The user guide must sit at ../guide/user ` +
      "relative to frontend/ (Docker builds copy it there — see the root Dockerfile and docker-compose.yml).",
  );
  process.exit(1);
}

fs.rmSync(dest, { recursive: true, force: true });
let copied = 0;
for (const theme of ["dark", "light"]) {
  const from = path.join(src, theme);
  const to = path.join(dest, theme);
  fs.mkdirSync(to, { recursive: true });
  if (!fs.existsSync(from)) continue;
  for (const name of fs.readdirSync(from)) {
    if (!/\.(png|jpe?g|webp|svg)$/i.test(name)) continue;
    fs.copyFileSync(path.join(from, name), path.join(to, name));
    copied += 1;
  }
}
console.log(`sync-guide-assets: copied ${copied} image(s) to public/guide-assets`);
