"use client";

import Link from "next/link";
import { useState } from "react";
import { useRouter } from "next/navigation";
import {
  CloseCampaignDialog,
  StartCampaignDialog,
  useCampaignActions,
} from "@/components/campaign-actions";
import Notice from "@/components/notice";
import StatusPill from "@/components/status-pill";
import { Empty, ErrorBlock, Loading } from "@/components/state-ui";
import { apiGet } from "@/lib/api";
import {
  campaignDisplayName,
  campaignStatusPresentation,
  formatDate,
  formatNumber,
  formatPercent,
} from "@/lib/format";
import type { CampaignWithStats } from "@/lib/types";
import { useQuery } from "@/lib/use-query";

export default function CampaignsPage() {
  const router = useRouter();
  const [notice, setNotice] = useState<{ kind: "success" | "error"; text: string } | null>(null);
  const [startOpen, setStartOpen] = useState(false);
  const [closeOpen, setCloseOpen] = useState(false);
  const { busy, start, close } = useCampaignActions();

  const campaigns = useQuery<CampaignWithStats[]>(() => apiGet("/campaigns", { limit: 100 }), []);
  const active = campaigns.data?.find((c) => c.campaign.status === "active");

  async function handleStart(name?: string) {
    try {
      const result = await start(name);
      setStartOpen(false);
      setNotice({
        kind: "success",
        text: `Campaign "${campaignDisplayName(result.campaign.name, result.campaign.id)}" started.`,
      });
      campaigns.reload();
      router.push(`/campaigns/${result.campaign.id}`);
    } catch (err) {
      setNotice({ kind: "error", text: err instanceof Error ? err.message : "Could not start the campaign." });
    }
  }

  async function handleClose() {
    if (!active) return;
    try {
      const result = await close();
      setCloseOpen(false);
      setNotice({
        kind: "success",
        text: `Campaign "${campaignDisplayName(result.campaign.name, result.campaign.id)}" closed and finalised.`,
      });
      campaigns.reload();
    } catch (err) {
      setNotice({ kind: "error", text: err instanceof Error ? err.message : "Could not close the campaign." });
    }
  }

  return (
    <div className="page">
      <div className="page-head">
        <h1>Campaigns</h1>
        <div className="actions">
          {active && (
            <button className="btn btn-danger-secondary" onClick={() => setCloseOpen(true)}>
              Close active campaign
            </button>
          )}
          <button className="btn btn-primary" onClick={() => setStartOpen(true)}>
            Start campaign
          </button>
        </div>
      </div>

      {notice && (
        <Notice kind={notice.kind} dismissMs={6000}>
          {notice.text}
        </Notice>
      )}

      <section className="section">
        {campaigns.loading && !campaigns.data ? (
          <Loading text="Loading campaigns\u2026" />
        ) : campaigns.error ? (
          <div className="section-body">
            <ErrorBlock message="We couldn't load campaign data." onRetry={campaigns.reload} />
          </div>
        ) : !campaigns.data || campaigns.data.length === 0 ? (
          <Empty text="No campaigns yet." />
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Campaign</th>
                <th>Status</th>
                <th>Period</th>
                <th>Customers targeted</th>
                <th>SMS sent</th>
                <th>Conversions</th>
                <th>Conversion rate</th>
              </tr>
            </thead>
            <tbody>
              {campaigns.data.map((row) => {
                const c = row.campaign;
                const status = campaignStatusPresentation(c.status);
                const stats = row.stats;
                return (
                  <tr key={c.id} className={c.status === "active" ? "row-active" : undefined}>
                    <td className="small">
                      <Link className="table-link" href={`/campaigns/${c.id}`}>
                        {campaignDisplayName(c.name, c.id)}
                      </Link>
                    </td>
                    <td>
                      <StatusPill {...status} />
                    </td>
                    <td className="small muted">
                      {formatDate(c.started_at)}
                      {"\u2009\u2192\u2009"}
                      {c.ended_at ? formatDate(c.ended_at) : "present"}
                    </td>
                    <td className="small num">{formatNumber(stats.opportunities.total)}</td>
                    <td className="small num">{formatNumber(stats.interventions.total)}</td>
                    <td className="small num">{formatNumber(stats.interventions.responded ?? 0)}</td>
                    <td className="small num">{formatPercent(stats.response_rate)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </section>

      <StartCampaignDialog open={startOpen} busy={busy} onConfirm={handleStart} onCancel={() => setStartOpen(false)} />
      <CloseCampaignDialog open={closeOpen} busy={busy} onConfirm={handleClose} onCancel={() => setCloseOpen(false)} />
    </div>
  );
}