/**
 * GitHub-compatible heading anchor: the same `#fragment` works on the in-app `/guide` page
 * and when the Markdown is read on GitHub. Lowercase, punctuation dropped (hyphens kept),
 * each space becomes a hyphen — so "Profit booking / stop loss" → "profit-booking--stop-loss".
 */
export function headingSlug(text: string): string {
  return text
    .trim()
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\s_-]/gu, "")
    .replace(/\s/g, "-");
}

/** `## Heading` / `### Heading` lines of a Markdown document (fenced code skipped). */
export function markdownHeadings(markdown: string): { depth: number; text: string; slug: string }[] {
  const out: { depth: number; text: string; slug: string }[] = [];
  let inFence = false;
  for (const line of markdown.split("\n")) {
    if (/^\s*(```|~~~)/.test(line)) {
      inFence = !inFence;
      continue;
    }
    if (inFence) continue;
    const m = /^(#{1,6})\s+(.+?)\s*#*\s*$/.exec(line);
    if (!m) continue;
    const text = stripInlineMarkdown(m[2]);
    out.push({ depth: m[1].length, text, slug: headingSlug(text) });
  }
  return out;
}

/** Plain text of a heading's inline Markdown (emphasis, code, links) — what GitHub slugs. */
export function stripInlineMarkdown(s: string): string {
  return s
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/`([^`]*)`/g, "$1")
    .replace(/(\*\*|__)(.*?)\1/g, "$2")
    .replace(/(\*|_)(.*?)\1/g, "$2");
}
