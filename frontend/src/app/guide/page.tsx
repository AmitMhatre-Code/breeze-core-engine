import Link from "next/link";
import { GuideMarkdown } from "@/components/guide/GuideMarkdown";
import { formatIsoDateForGuide } from "@/lib/guide/format";
import { loadGuideManifest, readGuideIndexMarkdown } from "@/lib/guide/content";

export default function GuideIndexPage() {
  const manifest = loadGuideManifest();
  return (
    <main id="main-content" className="mx-auto w-full max-w-[860px] px-5 py-10 sm:px-8">
      <GuideMarkdown markdown={readGuideIndexMarkdown()} />
      <p className="mt-4 text-[0.8rem] text-faint">Last reviewed {formatIsoDateForGuide(manifest.reviewed)}</p>
      <div className="mt-10 space-y-10">
        {manifest.parts.map((part, pi) => (
          <section key={part.title} aria-labelledby={`part-${pi}`}>
            <h2 id={`part-${pi}`} className="mb-4 text-micro font-semibold uppercase tracking-[0.16em] text-faint">
              Part {pi + 1} · {part.title}
            </h2>
            <ul className="grid gap-3 sm:grid-cols-2">
              {part.sections.map((s) => (
                <li key={s.slug}>
                  <Link
                    href={`/guide/${s.slug}`}
                    className="app-card block h-full p-4 transition hover:border-accent/60 hover:bg-panel2"
                  >
                    <div className="text-[0.95rem] font-bold text-foreground">{s.title}</div>
                    <div className="mt-1 text-[0.84rem] leading-snug text-muted">{s.summary}</div>
                  </Link>
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>
    </main>
  );
}
