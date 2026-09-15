"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import StatusPill from "@/components/status-pill";
import StatSection from "@/components/statistics/section";
import {
  StatusFilter,
  type CampaignStatusFilter,
} from "@/components/statistics/status-filter";
import { Empty, ErrorBlock, Loading } from "@/components/state-ui";
import { apiGet } from "@/lib/api";
import {
  campaignDisplayName,
  campaignStatusPresentation,
  formatDate,
  formatDuration,
  formatNumber,
  formatPercent,
} from "@/lib/format";
import type { CampaignStatisticsSummary } from "@/lib/types";
import { useQuery } from "@/lib/use-query";

export default function StatisticsPage() {
  const [status, setStatus] = useState<CampaignStatusFilter>("all");

  const campaigns = useQuery<CampaignStatisticsSummary[]>(
    () => apiGet("/stats/campaigns", { limit: 200 }),
    [],
    30000,
  );

  const rows = useMemo(() => {
    const list = campaigns.data ?? [];
    if (status === "all") return list;
    return list.filter((c) => c.status === status);
  }, [campaigns.data, status]);

  return (
    <div className="page">
      <div className="page-head">
        <h1>Statistics</h1>
        <div className="actions">
          <button className="btn btn-secondary" onClick={campaigns.reload}>
            Refresh
          </button>
        </div>
      </div>

      <StatSection
        title="Campaign performance"
        aside={<StatusFilter value={status} onChange={setStatus} />}
      >
        {campaigns.loading && !campaigns.data ? (
          <Loading text="Loading campaign statistics\u2026" />
        ) : campaigns.error ? (
          <div className="section-body">
            <ErrorBlock
              message="We couldn't load campaign statistics."
              onRetry={campaigns.reload}
            />
          </div>
        ) : !rows || rows.length === 0 ? (
          <Empty
            text={
              campaigns.data?.length
                ? "No campaigns match this filter."
                : "No campaign statistics yet."
            }
          />
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Campaign</th>
                <th>Status</th>
                <th>Period</th>
                <th>Targeted</th>
                <th>Accepted</th>
                <th>Delivered</th>
                <th>Converted</th>
                <th>Conv. rate</th>
                <th>Avg time to convert</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((c) => {
                const status = campaignStatusPresentation(c.status);
                return (
                  <tr
                    key={c.campaign_id}
                    className={c.status === "active" ? "row-active" : undefined}
                  >
                    <td className="small">
                      <Link className="table-link" href={`/statistics/${c.campaign_id}`}>
                        {campaignDisplayName(c.name, c.campaign_id)}
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
                    <td
                      className="small num"
                      title={`${c.audience.opportunities} opportunities \u00b7 ${c.audience.unique_customers} unique customers`}
                    >
                      {formatNumber(c.audience.unique_customers)}
                    </td>
                    <td className="small num">{formatNumber(c.sms.accepted)}</td>
                    <td className="small num">{formatNumber(c.sms.delivered)}</td>
                    <td className="small num">{formatNumber(c.response.converted_customers)}</td>
                    <td className="small num">{formatPercent(c.response.conversion_rate)}</td>
                    <td className="small num">
                      {c.response.avg_response_seconds != null
                        ? formatDuration(c.response.avg_response_seconds)
                        : "\u2014"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </StatSection>
    </div>
  );
}