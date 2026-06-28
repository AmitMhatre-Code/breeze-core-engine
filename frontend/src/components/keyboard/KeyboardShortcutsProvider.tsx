"use client";

import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { usePathname, useRouter } from "next/navigation";
import { KeyboardShortcutsDialog } from "@/components/keyboard/KeyboardShortcutsDialog";
import { isTypingTarget } from "@/lib/ui/is-typing-target";

const TRADING_PATHS = new Set([
  "/place-order",
  "/basket-order",
  "/strategy-builder",
  "/trade-options-chain",
]);

const NAV_SHORTCUTS: { key: string; href: string }[] = [
  { key: "1", href: "/dashboard" },
  { key: "2", href: "/portfolio" },
  { key: "3", href: "/performance" },
  { key: "4", href: "/orders" },
  { key: "5", href: "/place-order" },
  { key: "6", href: "/basket-order" },
  { key: "7", href: "/strategy-builder" },
  { key: "8", href: "/settings" },
];

type KeyboardShortcutsContextValue = {
  openHelp: () => void;
};

const KeyboardShortcutsContext =
  createContext<KeyboardShortcutsContextValue | null>(null);

export function useKeyboardShortcuts(): KeyboardShortcutsContextValue {
  const ctx = useContext(KeyboardShortcutsContext);
  if (!ctx) {
    throw new Error(
      "useKeyboardShortcuts must be used within KeyboardShortcutsProvider",
    );
  }
  return ctx;
}

export function KeyboardShortcutsProvider({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [helpOpen, setHelpOpen] = useState(false);

  const openHelp = useCallback(() => setHelpOpen(true), []);

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.defaultPrevented) return;
      if (e.metaKey || e.ctrlKey) return;

      if (e.key === "?" && !isTypingTarget(e.target)) {
        e.preventDefault();
        setHelpOpen(true);
        return;
      }

      if (e.altKey && !isTypingTarget(e.target)) {
        const nav = NAV_SHORTCUTS.find((s) => s.key === e.key);
        if (nav) {
          e.preventDefault();
          router.push(nav.href);
          return;
        }
      }

      if (
        e.key === "/" &&
        !e.altKey &&
        !isTypingTarget(e.target) &&
        TRADING_PATHS.has(pathname)
      ) {
        e.preventDefault();
        const scrip = document.querySelector<HTMLElement>(
          "[data-scrip-input]",
        );
        scrip?.focus();
      }
    };

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [pathname, router]);

  return (
    <KeyboardShortcutsContext.Provider value={{ openHelp }}>
      {children}
      <KeyboardShortcutsDialog open={helpOpen} onClose={() => setHelpOpen(false)} />
    </KeyboardShortcutsContext.Provider>
  );
}
