"use client";

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { usePathname } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { LoginDisclosureDialog } from "@/components/login-disclosure/LoginDisclosureDialog";
import { fetchAuthSession } from "@/lib/auth-session";
import { fetchDashboardBootstrap } from "@/lib/dashboard-bootstrap";
import { acceptLoginDisclosure, fetchLoginDisclosureCurrent } from "@/lib/login-disclosure";
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

  const disclosureQ = useQuery({
    queryKey: ["login-disclosure", "current"],
    queryFn: fetchLoginDisclosureCurrent,
    enabled: enabled && authed,
    staleTime: 0,
    refetchOnMount: "always",
    retry: false,
  });

  const version = disclosureQ.data?.version ?? null;

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

  const portalConfigured = disclosureQ.data?.portal_configured !== false;
  const hasDisclosure = disclosureQ.isSuccess && version != null;
  const needsDisclosure = Boolean(
    enabled &&
      authed &&
      hasDisclosure &&
      portalConfigured &&
      !sessionAcked &&
      !disclosureQ.isError,
  );

  const blocksApp = Boolean(
    enabled &&
      !onTermsPage &&
      (pendingLogin ||
        (!sessionAcked &&
          authed &&
          (disclosureQ.isLoading ||
            (hasDisclosure && portalConfigured && !disclosureQ.isError)))),
  );

  useEffect(() => {
    if (!pendingLogin || !authed || disclosureQ.isLoading) return;
    if (!needsDisclosure) {
      clearDisclosurePending();
      setPendingLogin(false);
    }
  }, [pendingLogin, authed, disclosureQ.isLoading, needsDisclosure]);

  useEffect(() => {
    if (!needsDisclosure || disclosureQ.isLoading || !hasDisclosure) return;
    void queryClient.prefetchQuery({
      queryKey: ["dashboard", "bootstrap"],
      queryFn: fetchDashboardBootstrap,
      staleTime: 30_000,
    });
  }, [needsDisclosure, disclosureQ.isLoading, hasDisclosure, queryClient]);

  const showDisclosureDialog = blocksApp;
  const contentMarkdown = disclosureQ.data?.content_markdown ?? "";

  return (
    <LoginDisclosureContext.Provider
      value={{
        needsDisclosure,
        isLoading: enabled && (sessionQ.isLoading || disclosureQ.isLoading),
        blocksApp,
      }}
    >
      {blocksApp ? null : children}
      <LoginDisclosureDialog
        open={showDisclosureDialog}
        pending={acceptMut.isPending}
        contentMarkdown={contentMarkdown || "Loading risk disclosure…"}
        version={version}
        effectiveDate={disclosureQ.data?.effective_date ?? null}
        onProceed={() => acceptMut.mutate()}
      />
    </LoginDisclosureContext.Provider>
  );
}
