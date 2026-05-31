import Link from "next/link";
import { AppShell } from "@/components/layout/AppShell";

export default function SettingsPage() {
  return (
    <AppShell>
      <section className="app-card space-y-3 p-4">
        <header className="flex items-center justify-between">
          <h2 className="app-text-title">Settings</h2>
        </header>
        <div className="grid gap-4 md:grid-cols-2">
          <div className="app-card-muted space-y-2 p-4 text-sm">
            <div className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
              Broker Credentials
            </div>
            <p className="app-text-muted">
              Update your ICICI API key and secret fragment.
            </p>
            <Link href="/settings/credentials" className="app-btn-outline">
              Update credentials
            </Link>
          </div>
          <div className="app-card-muted space-y-2 p-4 text-sm">
            <div className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
              Quantity Limits
            </div>
            <p className="app-text-muted">
              Update the scrip wise freeze quantities / max order quantities.
            </p>
            <Link href="/settings/quantity-limits" className="app-btn-outline">
              Update quantity limits
            </Link>
          </div>
          <div className="app-card-muted space-y-2 p-4 text-sm">
            <div className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
              API Usage
            </div>
            <p className="app-text-muted">
              Review API call volumes to stay within ICICI&apos;s daily usage limits.
            </p>
            <Link href="/settings/api-usage" className="app-btn-outline">
              Open API usage
            </Link>
          </div>
          <div className="app-card-muted space-y-2 p-4 text-sm">
            <div className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
              GenAI Settings
            </div>
            <p className="app-text-muted">
              Configure your own Gemini or OpenAI API key for GenAI market
              outlooks.
            </p>
            <Link href="/settings/ai-provider" className="app-btn-outline">
              Configure AI provider
            </Link>
          </div>
          <div className="app-card-muted space-y-2 p-4 text-sm">
            <div className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
              Margin Calculation Source
            </div>
            <p className="app-text-muted">
              Choose between Breeze API and Exchange Risk Baseline for margin calculations in Strategy Builder. Refresh the Baseline from NSE to get the latest data.
            </p>
            <Link href="/settings/margin-source" className="app-btn-outline">
              Update margin source
            </Link>
          </div>
          <div className="app-card-muted space-y-2 p-4 text-sm">
            <div className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">Scrip Master</div>
            <p className="app-text-muted">Inspect scrip data age/expiry health and load latest scrip master from ICICI.</p>
            <Link href="/settings/scrip-master" className="app-btn-outline">
              Update scrip master
            </Link>
          </div>
          <div className="rounded-md border border-red-300 bg-red-50/60 p-4 text-sm dark:border-red-900/50 dark:bg-red-950/20">
            <div className="font-bold text-red-900 dark:text-red-200">Breeze API Playground</div>
            <p className="mt-1 text-zinc-700 dark:text-zinc-400">
              Test-fire raw ICICI Breeze REST APIs with your live session. Can place, modify, or cancel
              real orders — use only if you understand the risk.
            </p>
            <Link
              href="/settings/breeze-api-playground"
              className="mt-3 inline-flex rounded-sm border border-red-800 bg-white px-3 py-2 text-sm font-medium text-red-900 hover:bg-red-50 dark:border-red-700 dark:bg-red-950/40 dark:text-red-100 dark:hover:bg-red-950/70"
            >
              Open API playground…
            </Link>
          </div>
          <div className="md:col-span-2 rounded-md border border-red-200 bg-red-50/50 p-4 text-sm dark:border-red-900/40 dark:bg-red-950/15">
            <div className="font-bold text-red-900 dark:text-red-200">
              Delete Account
            </div>
            <p className="mt-1 text-zinc-700 dark:text-zinc-400">
              Permanently remove your account and stored credentials using your app password. This only removes your account from the Breeze Core Engine. It does not delete your account from ICICI nor does it release any AWS resources. To release your AWS resources, login to breeze-ui.com and follow the instructions in the license console to release your AWS resources.
            </p>
            <Link
              href="/settings/delete-account"
              className="mt-3 inline-flex rounded-sm border border-red-800 bg-white px-3 py-2 text-sm font-medium text-red-900 hover:bg-red-50 dark:border-red-700 dark:bg-red-950/40 dark:text-red-100 dark:hover:bg-red-950/70"
            >
              Delete account…
            </Link>
          </div>
        </div>
      </section>
    </AppShell>
  );
}
