"use client";

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { Modal } from "@/components/ui/Modal";
import {
  LICENSE_CONSOLE_URL,
  licenseBannerMessage,
  type DeploymentLicenseStatus,
} from "@/lib/deployment-license";
import { buildContactSalesMailtoForLicenseStatus } from "@/lib/contact-sales-mailto";
import {
  licenseStatusFromQuery,
  tradingReadOnlyFromLicense,
  useDeploymentLicense,
} from "@/lib/use-deployment-license";

type LicenseRestrictionContextValue = {
  licenseStatus: DeploymentLicenseStatus | null;
  tradingReadOnly: boolean;
  contactSalesMailto: string | null;
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
  contactSalesMailto,
  licenseStatus,
}: {
  open: boolean;
  onClose: () => void;
  contactSalesMailto: string | null;
  licenseStatus: DeploymentLicenseStatus | null;
}) {
  const banner =
    licenseBannerMessage(licenseStatus) ??
    licenseBannerMessage("revoked") ??
    "";
  const [before, after] = banner.split("breeze-ui.com");
  const title =
    licenseStatus === "unlicensed" ? "No deployment license" : "License revoked";

  return (
    <Modal
      open={open}
      onClose={onClose}
      role="alertdialog"
      dismissible={false}
      titleId="license-revoked-title"
      zIndexClass="z-[100]"
      panelClassName="max-w-lg rounded-lg border border-red-200 bg-white p-5 shadow-xl dark:border-red-500/35 dark:bg-zinc-900"
    >
      <h2
        id="license-revoked-title"
        className="text-base font-semibold text-zinc-900 dark:text-zinc-100"
      >
        {title}
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
      <div className="mt-4 flex flex-wrap justify-end gap-2">
        {contactSalesMailto ? (
          <a
            href={contactSalesMailto}
            data-license-allow
            className="rounded-md border border-red-300 px-4 py-2 text-sm font-medium text-red-900 hover:bg-red-50 dark:border-red-500/40 dark:text-red-100 dark:hover:bg-red-950/40"
          >
            Contact Sales
          </a>
        ) : null}
        <button
          type="button"
          data-license-allow
          className="rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white hover:bg-zinc-800 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-white"
          onClick={onClose}
        >
          OK
        </button>
      </div>
    </Modal>
  );
}

export function LicenseRestrictionProvider({ children }: { children: ReactNode }) {
  const licenseQ = useDeploymentLicense();

  const licenseStatus = licenseStatusFromQuery(licenseQ.data);
  const tradingReadOnly = tradingReadOnlyFromLicense(licenseQ.data);
  const contactSalesMailto = buildContactSalesMailtoForLicenseStatus(
    licenseStatus,
    licenseQ.data?.contact_sales,
  );

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
      contactSalesMailto,
      showRevokedDialog,
      guardTradingAction,
    }),
    [licenseStatus, tradingReadOnly, contactSalesMailto, showRevokedDialog, guardTradingAction],
  );

  return (
    <LicenseRestrictionContext.Provider value={value}>
      {children}
      <RevokedLicenseDialog
        open={dialogOpen}
        onClose={() => setDialogOpen(false)}
        contactSalesMailto={contactSalesMailto}
        licenseStatus={licenseStatus}
      />
    </LicenseRestrictionContext.Provider>
  );
}
