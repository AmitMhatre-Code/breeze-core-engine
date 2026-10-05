import Link from "next/link";
import type { ReactNode } from "react";
import Markdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { guideImageSize } from "@/lib/guide/content";
import { headingSlug } from "@/lib/guide/slug";

/**
 * Renders one user-guide section (Markdown from guide/user/). The source is written to read well on
 * GitHub too, so this maps GitHub conventions onto the app:
 * - `[text](other-section.md#anchor)` → `/guide/other-section#anchor`
 * - `![alt](images/dark/name.png "caption")` → the dark and light captures of `name.png`, swapped by
 *   the app theme (copied into /guide-assets by scripts/sync-guide-assets.mjs), each shown at its
 *   own pixel size and only scaled down when wider than the column
 * - `> [!NOTE]` / `[!TIP]` / `[!IMPORTANT]` / `[!WARNING]` / `[!CAUTION]` → callout boxes
 * - heading ids use GitHub's slug rule, so the same fragment works in both places
 */

type MdNode = {
  type: string;
  value?: string;
  children?: MdNode[];
  data?: { hProperties?: Record<string, string> };
};

const CALLOUT_KINDS = ["note", "tip", "important", "warning", "caution"] as const;
type CalloutKind = (typeof CALLOUT_KINDS)[number];

function remarkCallouts() {
  const walk = (node: MdNode) => {
    if (node.type === "blockquote") {
      const para = node.children?.[0];
      const first = para?.type === "paragraph" ? para.children?.[0] : undefined;
      const m = first?.type === "text" ? /^\[!(\w+)\]\s*/.exec(first.value ?? "") : null;
      const kind = m?.[1].toLowerCase() as CalloutKind | undefined;
      if (first && m && kind && CALLOUT_KINDS.includes(kind)) {
        first.value = (first.value ?? "").slice(m[0].length);
        node.data = { ...node.data, hProperties: { "data-callout": kind } };
      }
    }
    node.children?.forEach(walk);
  };
  return (tree: MdNode) => walk(tree);
}

function textOf(children: ReactNode): string {
  if (children == null || typeof children === "boolean") return "";
  if (typeof children === "string" || typeof children === "number") return String(children);
  if (Array.isArray(children)) return children.map(textOf).join("");
  if (typeof children === "object" && "props" in children) {
    return textOf((children as { props: { children?: ReactNode } }).props.children);
  }
  return "";
}

function guideHref(href: string): string {
  if (/^[a-z]+:/i.test(href) || href.startsWith("/") || href.startsWith("#")) return href;
  const m = /^(?:\.\/)?([a-z0-9-]+)\.md(#.*)?$/i.exec(href);
  return m ? `/guide/${m[1]}${m[2] ?? ""}` : href;
}

function screenshotPair(src: string): { light: string; dark: string; name: string } | null {
  const m = /^(?:\.\/)?images\/(?:dark|light)\/(.+)$/.exec(src);
  return m ? { light: `/guide-assets/light/${m[1]}`, dark: `/guide-assets/dark/${m[1]}`, name: m[1] } : null;
}

// Text width of the guide column: max-w-[860px] less sm:px-8 in app/guide/[slug]/page.tsx.
const COLUMN_WIDTH = 796;

function Screenshot({ src, alt, theme, name, className }: {
  src: string;
  alt: string;
  theme: "dark" | "light";
  name: string;
  className: string;
}) {
  const size = guideImageSize(theme, name);
  // Real size, capped to the column: a small capture is never blown up past how it looked on screen.
  const img = (
    // eslint-disable-next-line @next/next/no-img-element -- static screenshots, sizes vary
    <img
      src={src}
      alt={alt}
      loading="lazy"
      width={size?.width}
      height={size?.height}
      className="mx-auto block h-auto max-w-full rounded-lg border border-border"
    />
  );
  // Only a capture the column scales down is worth opening full size.
  if (size && size.width <= COLUMN_WIDTH) return <span className={className}>{img}</span>;
  return (
    <a href={src} target="_blank" rel="noopener" className={className} title="Open full size">
      {img}
    </a>
  );
}

const CALLOUT_STYLE: Record<CalloutKind, { label: string; box: string; title: string }> = {
  note: { label: "Note", box: "border-accent/40 bg-accent-tint", title: "text-accent-on-tint" },
  tip: { label: "Tip", box: "border-up/40 bg-up-tint", title: "text-up-on-tint" },
  important: { label: "Important", box: "border-gtt/40 bg-gtt-tint", title: "text-gtt-on-tint" },
  warning: { label: "Warning", box: "border-amber-accent/50 bg-amber-tint", title: "text-amber-on-tint" },
  caution: { label: "Caution", box: "border-down/50 bg-down-tint", title: "text-down-on-tint" },
};

function Heading({
  level,
  children,
}: {
  level: 2 | 3 | 4;
  children: ReactNode;
}) {
  const id = headingSlug(textOf(children));
  const Tag = `h${level}` as const;
  const cls =
    level === 2
      ? "mt-12 mb-4 border-b border-border pb-2 text-[1.35rem] font-bold tracking-tight text-foreground"
      : level === 3
        ? "mt-8 mb-3 text-[1.08rem] font-bold text-foreground"
        : "mt-6 mb-2 text-[0.95rem] font-semibold text-foreground";
  return (
    <Tag id={id} className={`group scroll-mt-20 ${cls}`}>
      {children}
      <a
        href={`#${id}`}
        aria-label="Link to this section"
        className="ms-2 text-faint no-underline opacity-0 transition group-hover:opacity-100 focus:opacity-100"
      >
        #
      </a>
    </Tag>
  );
}

const components: Components = {
  h1: ({ children }) => (
    <h1 className="mb-2 text-[1.9rem] font-bold leading-tight tracking-tight text-foreground">{children}</h1>
  ),
  h2: ({ children }) => <Heading level={2}>{children}</Heading>,
  h3: ({ children }) => <Heading level={3}>{children}</Heading>,
  h4: ({ children }) => <Heading level={4}>{children}</Heading>,
  p: ({ node, children }) => {
    const only = node?.children?.length === 1 ? node.children[0] : null;
    const isImage = only?.type === "element" && only.tagName === "img";
    return isImage ? <div className="my-6">{children}</div> : <p className="my-3.5">{children}</p>;
  },
  a: ({ href, children }) => {
    const to = guideHref(href ?? "#");
    if (to.startsWith("/")) {
      return (
        <Link href={to} className="app-link underline">
          {children}
        </Link>
      );
    }
    if (to.startsWith("#")) {
      return (
        <a href={to} className="app-link underline">
          {children}
        </a>
      );
    }
    return (
      <a href={to} target="_blank" rel="noopener noreferrer" className="app-link underline">
        {children}
      </a>
    );
  },
  img: ({ src, alt, title }) => {
    const pair = typeof src === "string" ? screenshotPair(src) : null;
    return (
      <span className="block">
        {pair ? (
          <>
            <Screenshot src={pair.light} alt={alt ?? ""} theme="light" name={pair.name} className="block dark:hidden" />
            <Screenshot src={pair.dark} alt={alt ?? ""} theme="dark" name={pair.name} className="hidden dark:block" />
          </>
        ) : (
          // eslint-disable-next-line @next/next/no-img-element -- static guide images
          <img
            src={typeof src === "string" ? src : ""}
            alt={alt ?? ""}
            loading="lazy"
            className="mx-auto block h-auto max-w-full rounded-lg border border-border"
          />
        )}
        {title ? <span className="mt-2 block text-center text-[0.8rem] text-muted">{title}</span> : null}
      </span>
    );
  },
  ul: ({ children }) => <ul className="my-3.5 list-disc space-y-1.5 ps-6 marker:text-faint">{children}</ul>,
  ol: ({ children }) => <ol className="my-3.5 list-decimal space-y-1.5 ps-6 marker:text-faint">{children}</ol>,
  li: ({ children }) => <li className="ps-1">{children}</li>,
  strong: ({ children }) => <strong className="font-semibold text-foreground">{children}</strong>,
  hr: () => <hr className="my-10 border-border" />,
  code: ({ children, className }) =>
    className ? (
      <code className={className}>{children}</code>
    ) : (
      <code className="rounded bg-panel2 px-1.5 py-0.5 font-mono text-[0.85em] text-foreground">{children}</code>
    ),
  pre: ({ children }) => (
    <pre className="my-4 overflow-x-auto rounded-lg border border-border bg-panel2 p-4 font-mono text-[0.82rem] leading-relaxed">
      {children}
    </pre>
  ),
  table: ({ children }) => (
    <div className="my-5 overflow-x-auto rounded-lg border border-border">
      <table className="w-full border-collapse text-left text-[0.88rem]">{children}</table>
    </div>
  ),
  thead: ({ children }) => <thead className="bg-panel2 text-foreground">{children}</thead>,
  th: ({ children }) => <th className="border-b border-border px-3 py-2 align-bottom font-semibold">{children}</th>,
  td: ({ children }) => <td className="border-t border-border-soft px-3 py-2 align-top">{children}</td>,
  blockquote: ({ children, ...props }) => {
    const kind = (props as Record<string, unknown>)["data-callout"] as CalloutKind | undefined;
    if (kind && CALLOUT_STYLE[kind]) {
      const s = CALLOUT_STYLE[kind];
      return (
        <div className={`my-5 rounded-lg border-l-4 px-4 py-3 ${s.box}`} role="note">
          <div className={`mb-1 text-[0.8rem] font-bold uppercase tracking-wide ${s.title}`}>{s.label}</div>
          <div className="[&>p:first-child]:mt-0 [&>p:last-child]:mb-0">{children}</div>
        </div>
      );
    }
    return (
      <blockquote className="my-5 border-l-4 border-border ps-4 text-muted [&>p:first-child]:mt-0">{children}</blockquote>
    );
  },
};

export function GuideMarkdown({ markdown }: { markdown: string }) {
  return (
    <div className="text-[0.95rem] leading-[1.7] text-foreground/90">
      <Markdown remarkPlugins={[remarkGfm, remarkCallouts]} components={components}>
        {markdown}
      </Markdown>
    </div>
  );
}
