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
  // Not computed in useState's initializer: iciciHandoffGuideUrlForCurrentDeployment()
  // reads window.location, which only exists on the client. Doing that eagerly here
  // would make the very first client render differ from the server's (which always
  // sees no window and renders nothing) -- a hydration mismatch. The effect below
  // runs client-only, after hydration has already committed a matching empty render.
  const [href, setHref] = useState<string | null>(null);

  useEffect(() => {
    if (href) return;
    const guideUrl = iciciHandoffGuideUrlForCurrentDeployment();
    if (guideUrl) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- post-hydration read of window.location, see comment above
      setHref(guideUrl);
      return;
    }
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
