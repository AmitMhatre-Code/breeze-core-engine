"use client";

import { CampaignCard, CampaignChecks, CampaignLedger } from "@/components/condor/CondorCampaignPanel";
import { useCondorCampaign } from "@/lib/condor";

/** What a Dynamic Iron Condor campaign row expands into (#73).
 *
 *  While the row runs, it is the campaign card itself: the same one a live campaign shows on its
 *  Portfolio group and a Simulation one used to open from the bot card. Once the row has ended
 *  (closed, or handed back to the user), only the record: the engine is not run on a campaign
 *  the bot no longer manages. */
export function CondorCampaignRun({ campaignId, running }: { campaignId: string; running: boolean }) {
  const { data, isLoading, isError, error } = useCondorCampaign(campaignId);

  if (isLoading) return <p className="app-text-muted px-3 py-2 text-xs">Loading campaign…</p>;
  if (isError || !data) {
    return (
      <p className="px-3 py-2 text-xs text-down">
        Could not load the campaign: {(error as Error)?.message ?? "it no longer exists"}
      </p>
    );
  }
  if (running && data.status === "active") {
    return (
      <div className="p-3">
        <CampaignCard campaign={data} request={null} />
      </div>
    );
  }
  return (
    <div className="space-y-2 p-3">
      <p className="text-xs text-muted">
        {data.status === "active"
          ? "Handed to you: manage it from its card on Portfolio."
          : `Closed ${data.closed_at ?? ""}${data.close_reason ? ` · ${data.close_reason.replace(/_/g, " ")}` : ""}.`}
      </p>
      <CampaignLedger fills={data.fills} />
      <CampaignChecks decisions={data.decisions} />
    </div>
  );
}
