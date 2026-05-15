"use client";

import {
  createContext,
  useCallback,
  useContext,
  useLayoutEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

export const THEME_STORAGE_KEY = "breeze-core-engine-theme";

export type ThemeMode = "light" | "dark";

type Ctx = {
  theme: ThemeMode;
  isDark: boolean;
  setTheme: (mode: ThemeMode) => void;
  toggleTheme: () => void;
};

const ThemeContext = createContext<Ctx | null>(null);

function readTheme(): ThemeMode {
  const stored = localStorage.getItem(THEME_STORAGE_KEY) as ThemeMode | null;
  const prefersDark = window.matchMedia(
    "(prefers-color-scheme: dark)",
  ).matches;
  if (stored === "light" || stored === "dark") return stored;
  return prefersDark ? "dark" : "light";
}

function applyDomTheme(mode: ThemeMode) {
  document.documentElement.classList.toggle("dark", mode === "dark");
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<ThemeMode>("dark");

  useLayoutEffect(() => {
    const initial = readTheme();
    applyDomTheme(initial);
    // eslint-disable-next-line react-hooks/set-state-in-effect -- sync after layout boot script
    setThemeState(initial);
  }, []);

  const setTheme = useCallback((mode: ThemeMode) => {
    setThemeState(mode);
    localStorage.setItem(THEME_STORAGE_KEY, mode);
    applyDomTheme(mode);
  }, []);

  const toggleTheme = useCallback(() => {
    setThemeState((t) => {
      const next: ThemeMode = t === "dark" ? "light" : "dark";
      localStorage.setItem(THEME_STORAGE_KEY, next);
      applyDomTheme(next);
      return next;
    });
  }, []);

  const value = useMemo(
    () => ({
      theme,
      isDark: theme === "dark",
      setTheme,
      toggleTheme,
    }),
    [theme, setTheme, toggleTheme],
  );

  return (
    <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
  );
}

export function useThemeMode() {
  const ctx = useContext(ThemeContext);
  if (!ctx) {
    throw new Error("useThemeMode must be used within ThemeProvider");
  }
  return ctx;
}
