import { AppShell } from "@/components/layout/AppShell";
import { SettingsShell } from "@/components/settings/SettingsShell";

export default function SettingsPage() {
  return (
    <AppShell>
      <SettingsShell />
    </AppShell>
  );
}
