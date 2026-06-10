"use client";

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { usePathname } from "next/navigation";
import { useMutation, useQuery } from "@tanstack/react-query";
import { LoginDisclosureDialog } from "@/components/login-disclosure/LoginDisclosureDialog";
import { fetchAuthSession } from "@/lib/auth-session";
import { acceptLoginDisclosure, fetchLoginDisclosureCurrent } from "@/lib/login-disclosure";
import {
  hasSessionAck,
  setSessionAck,
} from "@/lib/login-disclosure-session";
import { shouldFetchLicenseHomeData } from "@/lib/public-auth-routes";

type LoginDisclosureContextValue = {
  needsDisclosure: boolean;
  isLoading: boolean;
};

const LoginDisclosureContext = createContext<LoginDisclosureContextValue>({
  needsDisclosure: false,
  isLoading: false,
});

export function useLoginDisclosure(): LoginDisclosureContextValue {
  return useContext(LoginDisclosureContext);
}

export function LoginDisclosureProvider({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const onTermsPage =
    pathname === "/terms-and-conditions" || pathname?.startsWith("/terms-and-conditions/");
  const enabled = shouldFetchLicenseHomeData(pathname);
  const [sessionAcked, setSessionAcked] = useState(false);

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
    if (!userId || version == null) {
      setSessionAcked(false);
      return;
    }
    setSessionAcked(hasSessionAck(userId, version));
  }, [userId, version]);

  const acceptMut = useMutation({
    mutationFn: async () => {
      if (version == null) throw new Error("Disclosure version unavailable");
      return acceptLoginDisclosure(version);
    },
    onSuccess: () => {
      if (userId && version != null) {
        setSessionAck(userId, version);
        setSessionAcked(true);
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

  const contentMarkdown = disclosureQ.data?.content_markdown ?? "";

  return (
    <LoginDisclosureContext.Provider
      value={{
        needsDisclosure,
        isLoading: enabled && (sessionQ.isLoading || disclosureQ.isLoading),
      }}
    >
      {children}
      <LoginDisclosureDialog
        open={needsDisclosure && !onTermsPage}
        pending={acceptMut.isPending}
        contentMarkdown={contentMarkdown || "Loading risk disclosure…"}
        version={version}
        effectiveDate={disclosureQ.data?.effective_date ?? null}
        onProceed={() => acceptMut.mutate()}
      />
    </LoginDisclosureContext.Provider>
  );
}
