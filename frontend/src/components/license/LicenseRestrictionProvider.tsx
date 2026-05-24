"use client";

import { usePathname } from "next/navigation";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { useQuery } from "@tanstack/react-query";
import { apiClient } from "@/lib/api-client";
import {
  isTradingReadOnly,
  LICENSE_CONSOLE_URL,
  LICENSE_REVOKED_BANNER,
  type DeploymentLicenseStatus,
} from "@/lib/deployment-license";
import type { HomeDataResponse } from "@/lib/home-data";

type LicenseRestrictionContextValue = {
  licenseStatus: DeploymentLicenseStatus | null;
  tradingReadOnly: boolean;
  showRevokedDialog: () => void;
  guardTradingAction: (fn: () => void) => void;
};

const LicenseRestrictionContext =
  createContext<LicenseRestrictionContextValue | null>(null);

export function useLicenseRestrictions(): LicenseRestrictionContextValue {
  const ctx = useContext(LicenseRestrictionContext);
  if (!ctx) {
    throw new Error(
      "useLicenseRestrictions must be used within LicenseRestrictionProvider",
    );
  }
  return ctx;
}

function RevokedLicenseDialog({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  useEffect(() => {
    if (!open) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, [open]);

  if (!open) return null;

  const [before, after] = LICENSE_REVOKED_BANNER.split("breeze-ui.com");

  return (
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center bg-black/45 p-4"
      role="presentation"
      onClick={onClose}
    >
      <div
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="license-revoked-title"
        className="max-w-lg rounded-lg border border-red-200 bg-white p-5 shadow-xl dark:border-red-500/35 dark:bg-zinc-900"
        onClick={(e) => e.stopPropagation()}
      >
        <h2
          id="license-revoked-title"
          className="text-base font-semibold text-zinc-900 dark:text-zinc-100"
        >
          License revoked
        </h2>
        <p className="mt-2 text-sm text-zinc-700 dark:text-zinc-300">
          {before}
          <a
            href={LICENSE_CONSOLE_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="font-medium text-red-800 underline underline-offset-2 dark:text-red-200"
          >
            breeze-ui.com
          </a>
          {after}
        </p>
        <div className="mt-4 flex justify-end">
          <button
            type="button"
            data-license-allow
            className="rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white hover:bg-zinc-800 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-white"
            onClick={onClose}
          >
            OK
          </button>
        </div>
      </div>
    </div>
  );
}

export function LicenseRestrictionProvider({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const licenseQueryEnabled = pathname !== "/login";

  const homeQ = useQuery({
    queryKey: ["home", "data"],
    queryFn: () => apiClient.get<HomeDataResponse>("/home/data"),
    staleTime: 30_000,
    enabled: licenseQueryEnabled,
    retry: false,
    refetchInterval: (query) => {
      const status = query.state.data?.deployment_license_status;
      return status === "expired" || status === "revoked" ? 60_000 : false;
    },
  });

  const licenseStatus = homeQ.data?.deployment_license_status ?? null;
  const tradingReadOnly =
    homeQ.data?.deployment_license_read_only === true ||
    isTradingReadOnly(licenseStatus);

  const [dialogOpen, setDialogOpen] = useState(false);

  const showRevokedDialog = useCallback(() => {
    setDialogOpen(true);
  }, []);

  const guardTradingAction = useCallback(
    (fn: () => void) => {
      if (tradingReadOnly) {
        showRevokedDialog();
        return;
      }
      fn();
    },
    [tradingReadOnly, showRevokedDialog],
  );

  const value = useMemo(
    () => ({
      licenseStatus,
      tradingReadOnly,
      showRevokedDialog,
      guardTradingAction,
    }),
    [licenseStatus, tradingReadOnly, showRevokedDialog, guardTradingAction],
  );

  return (
    <LicenseRestrictionContext.Provider value={value}>
      {children}
      <RevokedLicenseDialog
        open={dialogOpen}
        onClose={() => setDialogOpen(false)}
      />
    </LicenseRestrictionContext.Provider>
  );
}
