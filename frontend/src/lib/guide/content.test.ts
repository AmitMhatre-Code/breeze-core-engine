import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { helpTopics } from "@/lib/help/topics";
import {
  GUIDE_USER_DIR,
  listGuideSections,
  loadGuideManifest,
  readGuideIndexMarkdown,
  readGuideSectionMarkdown,
} from "./content";
import { headingSlug, markdownHeadings } from "./slug";

const manifest = loadGuideManifest();
const sections = listGuideSections(manifest);
const docs = new Map<string, string>([
  ["index", readGuideIndexMarkdown()],
  ...sections.map((s) => [s.slug, readGuideSectionMarkdown(s.slug, manifest)] as [string, string]),
]);
const anchorsOf = (slug: string) => new Set(markdownHeadings(docs.get(slug) ?? "").map((h) => h.slug));

describe("user guide content (guide/user)", () => {
  it("reads only from the repo-root guide/user folder", () => {
    expect(GUIDE_USER_DIR.split(path.sep).slice(-2)).toEqual(["guide", "user"]);
    // guide/technical is repo-only: nothing that builds the app may name it.
    for (const file of ["src/lib/guide/content.ts", "scripts/sync-guide-assets.mjs"]) {
      // Code only: the files' comments explain the rule and so name the folder.
      const code = fs
        .readFileSync(path.resolve(process.cwd(), file), "utf8")
        .replace(/\/\*[\s\S]*?\*\//g, "")
        .replace(/^\s*\/\/.*$/gm, "");
      expect(code, file).not.toMatch(/technical/);
    }
    expect(() => readGuideSectionMarkdown("../technical/architecture", manifest)).toThrow();
  });

  it("has a Markdown file for every manifest section, titled to match, and no strays", () => {
    const slugs = sections.map((s) => s.slug);
    expect(new Set(slugs).size).toBe(slugs.length);
    for (const s of sections) {
      const firstLine = (docs.get(s.slug) ?? "").split("\n")[0];
      expect(firstLine, s.slug).toBe(`# ${s.title}`);
    }
    const onDisk = fs
      .readdirSync(GUIDE_USER_DIR)
      .filter((f) => f.endsWith(".md"))
      .map((f) => f.replace(/\.md$/, ""))
      .filter((f) => f !== "index");
    expect(onDisk.sort()).toEqual([...slugs].sort());
  });

  it("has no duplicate heading anchors within a section", () => {
    for (const [slug, md] of docs) {
      const seen = markdownHeadings(md).map((h) => h.slug);
      const dupes = seen.filter((a, i) => seen.indexOf(a) !== i);
      expect(dupes, slug).toEqual([]);
    }
  });

  it("links only to sections and headings that exist", () => {
    const broken: string[] = [];
    for (const [slug, md] of docs) {
      for (const m of md.matchAll(/\]\((?!https?:|mailto:|\/)([^)\s]+)(?:\s+"[^"]*")?\)/g)) {
        const target = m[1];
        if (target.startsWith("images/")) continue;
        const [file, anchor] = target.split("#");
        const toSlug = file === "" ? slug : file.replace(/^\.\//, "").replace(/\.md$/, "");
        if (!docs.has(toSlug)) broken.push(`${slug}: ${target} (no such section)`);
        else if (anchor && !anchorsOf(toSlug).has(anchor)) broken.push(`${slug}: ${target} (no such heading)`);
      }
    }
    expect(broken).toEqual([]);
  });

  it("has every referenced screenshot in both the dark and light captures", () => {
    const missing: string[] = [];
    for (const [slug, md] of docs) {
      for (const m of md.matchAll(/!\[[^\]]*\]\(images\/(dark|light)\/([^)\s]+)/g)) {
        for (const theme of ["dark", "light"]) {
          const p = path.join(GUIDE_USER_DIR, "images", theme, m[2]);
          if (!fs.existsSync(p)) missing.push(`${slug}: images/${theme}/${m[2]}`);
        }
      }
    }
    expect(missing).toEqual([]);
  });

  it("points every Help topic's guide link at a real section and heading", () => {
    const broken: string[] = [];
    for (const t of helpTopics) {
      if (!t.guide) continue;
      const [slug, anchor] = t.guide.split("#");
      if (!docs.has(slug) || slug === "index") broken.push(`${t.id}: ${t.guide} (no such section)`);
      else if (anchor && !anchorsOf(slug).has(anchor)) broken.push(`${t.id}: ${t.guide} (no such heading)`);
    }
    expect(broken).toEqual([]);
    expect(helpTopics.every((t) => t.guide)).toBe(true);
  });
});

describe("headingSlug", () => {
  it("matches GitHub's anchor rule", () => {
    expect(headingSlug("Profit booking / stop loss")).toBe("profit-booking--stop-loss");
    expect(headingSlug("What's on the page")).toBe("whats-on-the-page");
    expect(headingSlug("API usage (5,000/day)")).toBe("api-usage-5000day");
  });
});
