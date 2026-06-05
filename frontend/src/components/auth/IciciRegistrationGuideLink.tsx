"use client";

import { useEffect, useState } from "react";
import { apiClient } from "@/lib/api-client";
import { iciciHandoffGuideUrlForCurrentDeployment } from "@/lib/icici-handoff-url";

type RegisterSession = {
  icici_handoff_url?: string | null;
};

export function IciciRegistrationGuideLink({
  className = "app-link",
}: {
  className?: string;
}) {
  const [href, setHref] = useState<string | null>(() => iciciHandoffGuideUrlForCurrentDeployment());

  useEffect(() => {
    if (href) return;
    void apiClient
      .get<RegisterSession>("/api/register/session", { sessionPolicy: "passive" })
      .then((session) => setHref(session.icici_handoff_url ?? null))
      .catch(() => {});
  }, [href]);

  if (!href) return null;

  return (
    <a href={href} target="_blank" rel="noopener noreferrer" className={className}>
      ICICI registration guide
    </a>
  );
}
