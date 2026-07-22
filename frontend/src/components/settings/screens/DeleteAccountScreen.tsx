"use client";

import { useQuery } from "@tanstack/react-query";
import { DeleteAccountWidget } from "@/components/settings/DeleteAccountWidget";
import { SettingsScreenHeader } from "@/components/settings/SettingsScreenHeader";
import { apiClient } from "@/lib/api-client";

type CredData = {
  user_id: string;
};

function DeleteIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M3 6h18" />
      <path d="M8 6V4h8v2" />
      <path d="M19 6l-1 14H6L5 6" />
      <path d="M10 11v6" />
      <path d="M14 11v6" />
    </svg>
  );
}

export function DeleteAccountScreen() {
  const q = useQuery({
    queryKey: ["settings", "credentials", "user_id_only"],
    queryFn: () => apiClient.get<CredData>("/api/settings/credentials/data"),
  });

  return (
    <div>
      <SettingsScreenHeader
        icon={<DeleteIcon />}
        danger
        title="Delete Account"
        description="Permanently remove your account and stored broker credentials."
      />
      <DeleteAccountWidget variant="settings" initialDirectUserId={q.data?.user_id} />
    </div>
  );
}
