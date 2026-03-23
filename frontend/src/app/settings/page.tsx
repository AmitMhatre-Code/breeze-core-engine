import Link from "next/link";
import { AppShell } from "@/components/layout/AppShell";

export default function SettingsPage() {
  return (
    <AppShell>
      <section className="app-card space-y-3 p-4">
        <header className="flex items-center justify-between">
          <h2 className="app-text-heading">Settings</h2>
        </header>
        <div className="grid gap-4 md:grid-cols-2">
          <div className="app-card-muted space-y-2 p-4 text-sm">
            <div className="font-medium text-zinc-900 dark:text-zinc-100">
              Broker credentials
            </div>
            <p className="app-text-muted">
              Update ICICI API key and secret fragment.
            </p>
            <Link href="/settings/credentials" className="app-btn-outline">
              Open credentials
            </Link>
          </div>
          <div className="app-card-muted space-y-2 p-4 text-sm">
            <div className="font-medium text-zinc-900 dark:text-zinc-100">
              Quantity limits
            </div>
            <p className="app-text-muted">
              Per-instrument caps from master data.
            </p>
            <Link href="/settings/quantity-limits" className="app-btn-outline">
              Open quantity limits
            </Link>
          </div>
          <div className="md:col-span-2 rounded-xl border border-red-200 bg-red-50/50 p-4 text-sm dark:border-red-900/40 dark:bg-red-950/15">
            <div className="font-medium text-red-900 dark:text-red-200">
              Delete account
            </div>
            <p className="mt-1 text-zinc-700 dark:text-zinc-400">
              Permanently remove your account and stored credentials. Works for both
              Google sign-in and app-password accounts.
            </p>
            <Link
              href="/settings/delete-account"
              className="mt-3 inline-flex rounded-lg border border-red-800 bg-white px-3 py-2 text-sm font-medium text-red-900 hover:bg-red-50 dark:border-red-700 dark:bg-red-950/40 dark:text-red-100 dark:hover:bg-red-950/70"
            >
              Delete account…
            </Link>
          </div>
        </div>
      </section>
    </AppShell>
  );
}
