"use client";

import { useEffect, useState } from "react";
import { HelpLink } from "@/components/help/HelpLink";
import { apiClient } from "@/lib/api-client";
import {
  buildBreezeUserHandoffGuideUrl,
  buildIciciStaticIpHandoffGuideUrl,
  deploymentPublicIpFromWindow,
} from "@/lib/icici-handoff-url";

type RegisterSession = {
  icici_handoff_url?: string | null;
};

export function IciciHandoffHelpLinks() {
  // Not computed in useState's initializer: deploymentPublicIpFromWindow() reads
  // window.location, which only exists on the client. Doing that eagerly here would
  // make the very first client render differ from the server's (which always sees
  // no window and renders nothing) -- a hydration mismatch. The effect below runs
  // client-only, after hydration has already committed a matching empty render.
  const [staticIpUrl, setStaticIpUrl] = useState<string | null>(null);
  const [userRegUrl, setUserRegUrl] = useState<string | null>(null);

  useEffect(() => {
    if (staticIpUrl && userRegUrl) return;

    const ip = deploymentPublicIpFromWindow();
    if (ip) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- post-hydration read of window.location, see comment above
      setStaticIpUrl((current) => current ?? buildIciciStaticIpHandoffGuideUrl(ip));
      setUserRegUrl((current) => current ?? buildBreezeUserHandoffGuideUrl(ip));
      return;
    }

    if (staticIpUrl) return;

    void apiClient
      .get<RegisterSession>("/api/register/session", { sessionPolicy: "passive" })
      .then((session) => {
        const href = session.icici_handoff_url ?? null;
        if (href) setStaticIpUrl(href);
      })
      .catch(() => {});
  }, [staticIpUrl, userRegUrl]);

  if (!staticIpUrl && !userRegUrl) return null;

  return (
    <div className="space-y-1 pt-2">
      <p className="text-center text-body text-muted">
        New to Breeze Modern?{" "}
        <HelpLink topicId="login-flow" className="text-body">
          Login &amp; registration help
        </HelpLink>
      </p>
      {staticIpUrl ? (
        <p className="text-center text-body text-muted">
          Need help with registration of Static IP? Read instructions{" "}
          <a href={staticIpUrl} target="_blank" rel="noopener noreferrer" className="app-link">
            here
          </a>
          .
        </p>
      ) : null}
      {userRegUrl ? (
        <p className="text-center text-body text-muted">
          Need help with registration and login? Read instructions{" "}
          <a href={userRegUrl} target="_blank" rel="noopener noreferrer" className="app-link">
            here
          </a>
          .
        </p>
      ) : null}
    </div>
  );
}
