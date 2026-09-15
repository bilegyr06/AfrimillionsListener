"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import StatusPill from "@/components/status-pill";
import CampaignReport from "@/components/statistics/campaign-report";
import { CampaignSelect } from "@/components/statistics/campaign-select";
import StatSection from "@/components/statistics/section";
import { ErrorBlock, Loading } from "@/components/state-ui";
import { apiGet } from "@/lib/api";
import { campaignDisplayName, campaignStatusPresentation } from "@/lib/format";
import type { CampaignStatisticsDetail, CampaignStatisticsSummary } from "@/lib/types";
import { useQuery } from "@/lib/use-query";

export default function CampaignStatisticsPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const campaignId = Number(params.id);

  const campaigns = useQuery<CampaignStatisticsSummary[]>(
    () => apiGet("/stats/campaigns", { limit: 200 }),
    [],
    30000,
  );

  const stats = useQuery<CampaignStatisticsDetail>(
    () => apiGet(`/stats/campaigns/${campaignId}`),
    [campaignId],
    30000,
  );

  if (stats.loading && !stats.data) {
    return (
      <div className="page">
        <div className="page-head">
          <h1>Statistics</h1>
        </div>
        <div className="section">
          <Loading text="Loading campaign statistics\u2026" />
        </div>
      </div>
    );
  }

  if (stats.error || !stats.data) {
    return (
      <div className="page">
        <div className="page-head">
          <h1>Statistics</h1>
          <Link className="btn btn-secondary" href="/statistics">
            Back to statistics
          </Link>
        </div>
        <div className="section">
          <div className="section-body">
            <ErrorBlock
              message="We couldn't load this campaign's statistics."
              onRetry={stats.reload}
            />
          </div>
        </div>
      </div>
    );
  }

  const { campaign } = stats.data;
  const status = campaignStatusPresentation(campaign.status);

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <h1>{campaignDisplayName(campaign.name, campaign.id)}</h1>
            <StatusPill {...status} />
          </div>
          <p className="muted" style={{ margin: "4px 0 0", fontSize: 13 }}>
            {stats.data.window.description}
          </p>
        </div>
        <div className="actions">
          <Link className="btn btn-secondary" href="/statistics">
            Back to statistics
          </Link>
          <Link className="btn btn-secondary" href={`/campaigns/${campaign.id}`}>
            View campaign
          </Link>
          <button className="btn btn-secondary" onClick={stats.reload}>
            Refresh
          </button>
        </div>
      </div>

      <StatSection
        title="Campaign"
        aside={
          <CampaignSelect
            campaigns={campaigns.data ?? []}
            value={campaignId}
            fallbackLabel={campaignDisplayName(campaign.name, campaign.id)}
            onChange={(next) => router.replace(`/statistics/${next}`)}
          />
        }
      >
        <CampaignReport detail={stats.data} />
      </StatSection>
    </div>
  );
}