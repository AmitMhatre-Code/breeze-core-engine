import fs from "node:fs";
import path from "node:path";
import { markdownHeadings } from "./slug";

/**
 * The user guide's source lives at the repo root in `guide/user/` and is read at build time only
 * (every /guide page is statically generated). This is the ONLY folder the app may read: its
 * sibling `guide/technical/` is repo-only engineering documentation — it names the license-bypass
 * env var and the DRM design — and must never be rendered or bundled. Sections are looked up by
 * slug through the manifest, never by a path taken from the URL.
 */
export const GUIDE_USER_DIR = path.resolve(process.cwd(), "..", "guide", "user");

export type GuideSection = { slug: string; title: string; summary: string };
export type GuidePart = { title: string; sections: GuideSection[] };
export type GuideManifest = { title: string; reviewed: string; parts: GuidePart[] };

export type GuideSectionRef = GuideSection & { part: string };

export function loadGuideManifest(): GuideManifest {
  const raw = fs.readFileSync(path.join(GUIDE_USER_DIR, "guide.json"), "utf8");
  return JSON.parse(raw) as GuideManifest;
}

/** All sections in reading order, each tagged with its part title. */
export function listGuideSections(manifest: GuideManifest = loadGuideManifest()): GuideSectionRef[] {
  return manifest.parts.flatMap((p) => p.sections.map((s) => ({ ...s, part: p.title })));
}

export function findGuideSection(
  slug: string,
  manifest: GuideManifest = loadGuideManifest(),
): { section: GuideSectionRef; prev: GuideSectionRef | null; next: GuideSectionRef | null } | null {
  const all = listGuideSections(manifest);
  const i = all.findIndex((s) => s.slug === slug);
  if (i < 0) return null;
  return { section: all[i], prev: all[i - 1] ?? null, next: all[i + 1] ?? null };
}

/** Markdown of one manifest section. Throws for a slug the manifest does not list. */
export function readGuideSectionMarkdown(slug: string, manifest: GuideManifest = loadGuideManifest()): string {
  if (!listGuideSections(manifest).some((s) => s.slug === slug)) {
    throw new Error(`Unknown guide section: ${slug}`);
  }
  return fs.readFileSync(path.join(GUIDE_USER_DIR, `${slug}.md`), "utf8");
}

/** The introduction shown above the contents on /guide (guide/user/index.md). */
export function readGuideIndexMarkdown(): string {
  return fs.readFileSync(path.join(GUIDE_USER_DIR, "index.md"), "utf8");
}

/**
 * Pixel size of one screenshot (`images/<theme>/<name>`), read from the PNG header. Captures are
 * taken at deviceScaleFactor 1, so this is also the size it had on screen. Null for anything that
 * is not a readable PNG.
 */
export function guideImageSize(theme: "dark" | "light", name: string): { width: number; height: number } | null {
  if (!/^[\w.-]+\.png$/i.test(name)) return null;
  try {
    const fd = fs.openSync(path.join(GUIDE_USER_DIR, "images", theme, name), "r");
    try {
      const head = Buffer.alloc(24);
      fs.readSync(fd, head, 0, 24, 0);
      if (head.toString("latin1", 12, 16) !== "IHDR") return null;
      return { width: head.readUInt32BE(16), height: head.readUInt32BE(20) };
    } finally {
      fs.closeSync(fd);
    }
  } catch {
    return null;
  }
}

/** Second-level headings, for the "On this page" list. */
export function sectionOutline(markdown: string): { text: string; slug: string }[] {
  return markdownHeadings(markdown)
    .filter((h) => h.depth === 2)
    .map(({ text, slug }) => ({ text, slug }));
}
