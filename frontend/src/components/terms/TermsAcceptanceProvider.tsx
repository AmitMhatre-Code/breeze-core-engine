"use client";

import { createContext, useContext, type ReactNode } from "react";
import { usePathname } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { TermsAcceptanceDialog } from "@/components/terms/TermsAcceptanceDialog";
import { acceptTerms, fetchTermsStatus } from "@/lib/terms";
import { shouldFetchLicenseHomeData } from "@/lib/public-auth-routes";

type TermsAcceptanceContextValue = {
  needsAcceptance: boolean;
  isLoading: boolean;
};

const TermsAcceptanceContext = createContext<TermsAcceptanceContextValue>({
  needsAcceptance: false,
  isLoading: false,
});

export function useTermsAcceptance(): TermsAcceptanceContextValue {
  return useContext(TermsAcceptanceContext);
}

export function TermsAcceptanceProvider({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const enabled = shouldFetchLicenseHomeData(pathname);
  const qc = useQueryClient();

  const statusQ = useQuery({
    queryKey: ["terms", "status"],
    queryFn: fetchTermsStatus,
    enabled,
    staleTime: 0,
    refetchOnMount: "always",
    retry: false,
  });

  const acceptMut = useMutation({
    mutationFn: async () => {
      const version = statusQ.data?.current_version;
      if (version == null) throw new Error("Terms version unavailable");
      return acceptTerms(version);
    },
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["terms", "status"] });
    },
  });

  const portalConfigured = statusQ.data?.portal_configured !== false;
  const needsAcceptance = Boolean(
    enabled && statusQ.isSuccess && portalConfigured && statusQ.data?.needs_acceptance,
  );

  const contentMarkdown = statusQ.data?.content_markdown ?? "";

  return (
    <TermsAcceptanceContext.Provider
      value={{
        needsAcceptance,
        isLoading: enabled && statusQ.isLoading,
      }}
    >
      {children}
      <TermsAcceptanceDialog
        open={needsAcceptance}
        pending={acceptMut.isPending}
        contentMarkdown={contentMarkdown || "Loading terms…"}
        version={statusQ.data?.current_version ?? null}
        effectiveDate={null}
        onAccept={() => acceptMut.mutate()}
      />
    </TermsAcceptanceContext.Provider>
  );
}
