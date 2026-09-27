import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { GuideMarkdown } from "@/components/guide/GuideMarkdown";
import {
  findGuideSection,
  listGuideSections,
  readGuideSectionMarkdown,
  sectionOutline,
} from "@/lib/guide/content";

// Every section is built at build time from guide/user/; anything else is a 404.
export const dynamicParams = false;

export function generateStaticParams() {
  return listGuideSections().map((s) => ({ slug: s.slug }));
}

export async function generateMetadata({ params }: { params: Promise<{ slug: string }> }): Promise<Metadata> {
  const { slug } = await params;
  const found = findGuideSection(slug);
  return found ? { title: found.section.title, description: found.section.summary } : {};
}

export default async function GuideSectionPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const found = findGuideSection(slug);
  if (!found) notFound();
  const { section, prev, next } = found;
  const markdown = readGuideSectionMarkdown(slug);
  const outline = sectionOutline(markdown);

  return (
    <div className="flex w-full">
      <main id="main-content" className="mx-auto w-full min-w-0 max-w-[860px] px-5 py-10 sm:px-8">
        <div className="mb-3 text-micro font-semibold uppercase tracking-[0.16em] text-faint">{section.part}</div>
        <GuideMarkdown markdown={markdown} />
        <nav aria-label="Previous and next sections" className="mt-14 grid gap-3 border-t border-border pt-6 sm:grid-cols-2">
          {prev ? (
            <Link href={`/guide/${prev.slug}`} className="app-card block p-4 transition hover:border-accent/60">
              <div className="text-micro font-semibold uppercase tracking-[0.14em] text-faint">← Previous</div>
              <div className="mt-1 font-semibold text-foreground">{prev.title}</div>
            </Link>
          ) : (
            <span />
          )}
          {next ? (
            <Link href={`/guide/${next.slug}`} className="app-card block p-4 text-right transition hover:border-accent/60">
              <div className="text-micro font-semibold uppercase tracking-[0.14em] text-faint">Next →</div>
              <div className="mt-1 font-semibold text-foreground">{next.title}</div>
            </Link>
          ) : null}
        </nav>
      </main>
      {outline.length > 1 ? (
        <aside className="sticky top-[57px] hidden max-h-[calc(100vh-57px)] w-[230px] shrink-0 overflow-y-auto py-10 pe-4 xl:block">
          <div className="mb-2 text-micro font-semibold uppercase tracking-[0.14em] text-faint">On this page</div>
          <ul className="space-y-1.5 border-l border-border text-[0.8rem]">
            {outline.map((h) => (
              <li key={h.slug}>
                <a href={`#${h.slug}`} className="-ml-px block border-l border-transparent ps-3 text-muted hover:border-accent hover:text-foreground">
                  {h.text}
                </a>
              </li>
            ))}
          </ul>
        </aside>
      ) : null}
    </div>
  );
}
