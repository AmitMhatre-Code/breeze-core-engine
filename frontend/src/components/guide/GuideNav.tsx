"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import type { GuidePart } from "@/lib/guide/content";

function NavList({ parts, pathname, onNavigate }: { parts: GuidePart[]; pathname: string; onNavigate?: () => void }) {
  return (
    <nav aria-label="Guide contents" className="space-y-6 text-[0.86rem]">
      <Link
        href="/guide"
        onClick={onNavigate}
        className={`block rounded-md px-2.5 py-1.5 font-semibold transition ${
          pathname === "/guide" ? "bg-accent-tint text-foreground" : "text-muted hover:bg-panel2 hover:text-foreground"
        }`}
      >
        Contents
      </Link>
      {parts.map((part) => (
        <div key={part.title}>
          <div className="mb-1.5 px-2.5 text-micro font-semibold uppercase tracking-[0.14em] text-faint">{part.title}</div>
          <ul className="space-y-0.5">
            {part.sections.map((s) => {
              const href = `/guide/${s.slug}`;
              const active = pathname === href;
              return (
                <li key={s.slug}>
                  <Link
                    href={href}
                    onClick={onNavigate}
                    aria-current={active ? "page" : undefined}
                    className={`relative block rounded-md px-2.5 py-1.5 transition ${
                      active
                        ? "bg-accent-tint font-semibold text-foreground"
                        : "text-muted hover:bg-panel2 hover:text-foreground"
                    }`}
                  >
                    {active ? (
                      <span aria-hidden className="absolute inset-y-[7px] left-0 w-[2.5px] rounded-full bg-accent-bar" />
                    ) : null}
                    {s.title}
                  </Link>
                </li>
              );
            })}
          </ul>
        </div>
      ))}
    </nav>
  );
}

/** Contents sidebar on wide screens; a collapsible "Contents" panel on phones and tablets. */
export function GuideNav({ parts }: { parts: GuidePart[] }) {
  const pathname = usePathname() ?? "/guide";
  const [open, setOpen] = useState(false);
  return (
    <>
      <aside className="sticky top-[57px] hidden max-h-[calc(100vh-57px)] w-[260px] shrink-0 overflow-y-auto border-r border-border px-3 py-6 lg:block">
        <NavList parts={parts} pathname={pathname} />
      </aside>
      <div className="border-b border-border px-4 py-2 lg:hidden">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          aria-controls="guide-mobile-contents"
          className="app-btn-outline min-h-10 w-full justify-between"
        >
          <span>Contents</span>
          <span aria-hidden>{open ? "▴" : "▾"}</span>
        </button>
        {open ? (
          <div id="guide-mobile-contents" className="mt-3 pb-3">
            <NavList parts={parts} pathname={pathname} onNavigate={() => setOpen(false)} />
          </div>
        ) : null}
      </div>
    </>
  );
}
