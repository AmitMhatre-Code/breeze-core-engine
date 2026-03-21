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
        </div>
      </section>
    </AppShell>
  );
}
