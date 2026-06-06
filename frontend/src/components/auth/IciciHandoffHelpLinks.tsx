"use client";

import { useEffect, useState } from "react";
import { apiClient } from "@/lib/api-client";
import {
  breezeUserHandoffUrlForCurrentDeployment,
  buildBreezeUserHandoffGuideUrl,
  buildIciciStaticIpHandoffGuideUrl,
  deploymentPublicIpFromWindow,
  iciciStaticIpHandoffUrlForCurrentDeployment,
} from "@/lib/icici-handoff-url";

type RegisterSession = {
  icici_handoff_url?: string | null;
};

export function IciciHandoffHelpLinks() {
  const [staticIpUrl, setStaticIpUrl] = useState<string | null>(() =>
    iciciStaticIpHandoffUrlForCurrentDeployment(),
  );
  const [userRegUrl, setUserRegUrl] = useState<string | null>(() =>
    breezeUserHandoffUrlForCurrentDeployment(),
  );

  useEffect(() => {
    if (staticIpUrl && userRegUrl) return;

    const ip = deploymentPublicIpFromWindow();
    if (ip) {
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
      {staticIpUrl ? (
        <p className="text-center text-[11px] text-zinc-500">
          Need help in registering Static IP with ICICI Direct? Read instructions{" "}
          <a href={staticIpUrl} target="_blank" rel="noopener noreferrer" className="app-link">
            here
          </a>
          .
        </p>
      ) : null}
      {userRegUrl ? (
        <p className="text-center text-[11px] text-zinc-500">
          Need help in registering user on this app? Read instructions{" "}
          <a href={userRegUrl} target="_blank" rel="noopener noreferrer" className="app-link">
            here
          </a>
          .
        </p>
      ) : null}
    </div>
  );
}
