"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { AppShell } from "@/components/layout/AppShell";
import { DeleteAccountWidget } from "@/components/settings/DeleteAccountWidget";
import { apiClient } from "@/lib/api-client";

type CredData = {
  user_id: string;
};

export default function SettingsDeleteAccountPage() {
  const q = useQuery({
    queryKey: ["settings", "credentials", "user_id_only"],
    queryFn: () => apiClient.get<CredData>("/api/settings/credentials/data"),
  });

  return (
    <AppShell>
      <div className="space-y-4">
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <Link href="/settings" className="app-link">
            Settings
          </Link>
          <span className="text-zinc-400">/</span>
          <span className="text-zinc-600 dark:text-zinc-400">Delete account</span>
        </div>
        <section className="app-card space-y-2 p-4">
          <h2 className="app-text-heading">Delete account</h2>
          <p className="app-text-muted max-w-2xl text-sm">
            Remove your Breeze Modern account and stored broker API credentials. Confirm with your
            ICICI user id and app password.
          </p>
        </section>
        <DeleteAccountWidget variant="settings" initialDirectUserId={q.data?.user_id} />
      </div>
    </AppShell>
  );
}
