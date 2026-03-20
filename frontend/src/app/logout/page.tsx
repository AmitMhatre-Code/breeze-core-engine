"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

export default function LogoutPage() {
  const router = useRouter();

  useEffect(() => {
    const run = async () => {
      try {
        await fetch("/auth/logout", {
          method: "POST",
          credentials: "include",
        });
      } catch {
        // Even if logout fails (e.g., expired session), still redirect.
      } finally {
        router.replace("/login");
      }
    };

    run();
  }, [router]);

  return (
    <div className="flex min-h-screen items-center justify-center bg-zinc-950 text-zinc-50">
      <div className="w-full max-w-md rounded-2xl border border-zinc-800 bg-zinc-900/80 p-8 text-sm text-zinc-300">
        Logging out...
      </div>
    </div>
  );
}

