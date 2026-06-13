"use client";

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { usePathname } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { LoginDisclosureDialog } from "@/components/login-disclosure/LoginDisclosureDialog";
import { fetchAuthSession } from "@/lib/auth-session";
import { fetchDashboardBootstrap } from "@/lib/dashboard-bootstrap";
import { acceptLoginDisclosure, fetchLoginDisclosureCurrent } from "@/lib/login-disclosure";
import { getPreloadedLoginDisclosure } from "@/lib/login-disclosure-preload";
import {
  clearDisclosurePending,
  hasSessionAck,
  isDisclosurePending,
  setSessionAck,
} from "@/lib/login-disclosure-session";
import { shouldFetchLicenseHomeData } from "@/lib/public-auth-routes";

type LoginDisclosureContextValue = {
  needsDisclosure: boolean;
  isLoading: boolean;
  /** True while the app must not mount protected page content (disclosure gate). */
  blocksApp: boolean;
};

const LoginDisclosureContext = createContext<LoginDisclosureContextValue>({
  needsDisclosure: false,
  isLoading: false,
  blocksApp: false,
});

export function useLoginDisclosure(): LoginDisclosureContextValue {
  return useContext(LoginDisclosureContext);
}

export function LoginDisclosureProvider({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const queryClient = useQueryClient();
  const onTermsPage =
    pathname === "/terms-and-conditions" || pathname?.startsWith("/terms-and-conditions/");
  const enabled = shouldFetchLicenseHomeData(pathname);
  const [justAccepted, setJustAccepted] = useState(false);
  const [pendingLogin, setPendingLogin] = useState(() => isDisclosurePending());

  useEffect(() => {
    setPendingLogin(isDisclosurePending());
  }, [pathname]);

  const sessionQ = useQuery({
    queryKey: ["auth", "session"],
    queryFn: fetchAuthSession,
    enabled,
    staleTime: 30_000,
    retry: false,
  });

  const authed = Boolean(sessionQ.data?.authenticated);
  const userId = (sessionQ.data?.user_id || "").trim().toUpperCase();
  const preloadedDoc = getPreloadedLoginDisclosure();

  const disclosureQ = useQuery({
    queryKey: ["login-disclosure", "current"],
    queryFn: fetchLoginDisclosureCurrent,
    enabled: enabled && authed,
    initialData: preloadedDoc ?? undefined,
    staleTime: preloadedDoc ? 60_000 : 0,
    refetchOnMount: preloadedDoc ? false : "always",
    retry: false,
  });

  const resolvedDoc = disclosureQ.data ?? preloadedDoc;
  const version = resolvedDoc?.version ?? null;

  useEffect(() => {
    setJustAccepted(false);
  }, [userId, version]);

  const sessionAcked =
    justAccepted ||
    (userId && version != null ? hasSessionAck(userId, version) : false);

  const acceptMut = useMutation({
    mutationFn: async () => {
      if (version == null) throw new Error("Disclosure version unavailable");
      return acceptLoginDisclosure(version);
    },
    onSuccess: () => {
      if (userId && version != null) {
        setSessionAck(userId, version);
        clearDisclosurePending();
        setPendingLogin(false);
        setJustAccepted(true);
      }
    },
  });

  const portalConfigured = resolvedDoc?.portal_configured !== false;
  const hasDisclosure = (disclosureQ.isSuccess || preloadedDoc != null) && version != null;
  const needsDisclosure = Boolean(
    enabled &&
      authed &&
      hasDisclosure &&
      portalConfigured &&
      !sessionAcked &&
      !disclosureQ.isError,
  );

  const disclosureLoading = disclosureQ.isLoading && preloadedDoc == null;

  const blocksApp = Boolean(
    enabled &&
      !onTermsPage &&
      (pendingLogin ||
        (!sessionAcked &&
          authed &&
          (disclosureLoading ||
            (hasDisclosure && portalConfigured && !disclosureQ.isError)))),
  );

  useEffect(() => {
    if (!pendingLogin || !authed || disclosureLoading) return;
    if (!needsDisclosure) {
      clearDisclosurePending();
      setPendingLogin(false);
    }
  }, [pendingLogin, authed, disclosureLoading, needsDisclosure]);

  useEffect(() => {
    if (!needsDisclosure || disclosureLoading || !hasDisclosure) return;
    void queryClient.prefetchQuery({
      queryKey: ["dashboard", "bootstrap"],
      queryFn: fetchDashboardBootstrap,
      staleTime: 30_000,
    });
  }, [needsDisclosure, disclosureLoading, hasDisclosure, queryClient]);

  const showDisclosureDialog = blocksApp;
  const contentMarkdown = resolvedDoc?.content_markdown ?? "";

  return (
    <LoginDisclosureContext.Provider
      value={{
        needsDisclosure,
        isLoading: enabled && (sessionQ.isLoading || disclosureLoading),
        blocksApp,
      }}
    >
      {blocksApp ? null : children}
      <LoginDisclosureDialog
        open={showDisclosureDialog}
        pending={acceptMut.isPending}
        contentMarkdown={contentMarkdown || "Loading risk disclosure…"}
        version={version}
        effectiveDate={resolvedDoc?.effective_date ?? null}
        onProceed={() => acceptMut.mutate()}
      />
    </LoginDisclosureContext.Provider>
  );
}
