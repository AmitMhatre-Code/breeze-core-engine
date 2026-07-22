"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { usePathname, useRouter } from "next/navigation";
import { HelpDialog } from "@/components/help/HelpDialog";
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

export type HelpTab = "topics" | "shortcuts";

type HelpContextValue = {
  openHelp: (topicId?: string, tab?: HelpTab) => void;
  closeHelp: () => void;
  helpOpen: boolean;
  activeTopicId: string | null;
  activeTab: HelpTab;
};

const HelpContext = createContext<HelpContextValue | null>(null);

export function useHelp(): HelpContextValue {
  const ctx = useContext(HelpContext);
  if (!ctx) {
    throw new Error("useHelp must be used within HelpProvider");
  }
  return ctx;
}

/** Optional hook — returns null outside provider (SSR-safe for HelpLink). */
export function useHelpOptional(): HelpContextValue | null {
  return useContext(HelpContext);
}

export function HelpProvider({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [helpOpen, setHelpOpen] = useState(false);
  const [activeTopicId, setActiveTopicId] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<HelpTab>("topics");

  const openHelp = useCallback((topicId?: string, tab: HelpTab = "topics") => {
    setActiveTab(topicId ? "topics" : tab);
    setActiveTopicId(topicId ?? null);
    setHelpOpen(true);
  }, []);

  const closeHelp = useCallback(() => {
    setHelpOpen(false);
    setActiveTopicId(null);
  }, []);

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.defaultPrevented) return;
      if (e.metaKey || e.ctrlKey) return;

      if (e.key === "?" && !isTypingTarget(e.target)) {
        e.preventDefault();
        openHelp(undefined, "topics");
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
        const scrip = document.querySelector<HTMLElement>("[data-scrip-input]");
        scrip?.focus();
      }
    };

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [pathname, router, openHelp]);

  return (
    <HelpContext.Provider
      value={{
        openHelp,
        closeHelp,
        helpOpen,
        activeTopicId,
        activeTab,
      }}
    >
      {children}
      <HelpDialog
        open={helpOpen}
        onClose={closeHelp}
        activeTopicId={activeTopicId}
        initialTab={activeTab}
      />
    </HelpContext.Provider>
  );
}
