"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import StatusPill from "@/components/status-pill";
import { CampaignComparison } from "@/components/statistics/campaign-comparison";
import CampaignReport from "@/components/statistics/campaign-report";
import { CampaignSelect } from "@/components/statistics/campaign-select";
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
import type { CampaignStatisticsDetail, CampaignStatisticsSummary } from "@/lib/types";
import { useQuery } from "@/lib/use-query";

export default function StatisticsPage() {
  const [status, setStatus] = useState<CampaignStatusFilter>("all");
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [compareIds, setCompareIds] = useState<number[]>([]);

  const campaigns = useQuery<CampaignStatisticsSummary[]>(
    () => apiGet("/stats/campaigns", { limit: 200 }),
    [],
    30000,
  );

  // Default to the current campaign when one is active, otherwise the most
  // recent (the backend serves summaries newest-first).
  useEffect(() => {
    if (selectedId != null) return;
    const list = campaigns.data ?? [];
    if (list.length === 0) return;
    const preferred = list.find((c) => c.status === "active") ?? list[0];
    setSelectedId(preferred.campaign_id);
  }, [campaigns.data, selectedId]);

  const detail = useQuery<CampaignStatisticsDetail | null>(
    () => (selectedId != null ? apiGet(`/stats/campaigns/${selectedId}`) : Promise.resolve(null)),
    [selectedId],
    30000,
  );

  const rows = useMemo(() => {
    const list = campaigns.data ?? [];
    if (status === "all") return list;
    return list.filter((c) => c.status === status);
  }, [campaigns.data, status]);

  const toggleCompare = (campaignId: number) => {
    setCompareIds((prev) =>
      prev.includes(campaignId)
        ? prev.filter((id) => id !== campaignId)
        : [...prev, campaignId],
    );
  };

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
        title="Campaign report"
        aside={
          <CampaignSelect
            campaigns={campaigns.data ?? []}
            value={selectedId}
            onChange={setSelectedId}
            disabled={campaigns.loading && !campaigns.data}
          />
        }
      >
        {campaigns.loading && !campaigns.data ? (
          <div className="section-body">
            <Loading text="Loading campaign statistics\u2026" />
          </div>
        ) : campaigns.error ? (
          <div className="section-body">
            <ErrorBlock
              message="We couldn't load campaign statistics."
              onRetry={campaigns.reload}
            />
          </div>
        ) : selectedId == null ? (
          <div className="section-body">
            <Empty text="No campaign statistics yet." />
          </div>
        ) : detail.loading && !detail.data ? (
          <div className="section-body">
            <Loading text="Loading campaign report\u2026" />
          </div>
        ) : detail.error || !detail.data ? (
          <div className="section-body">
            <ErrorBlock
              message="We couldn't load this campaign's report."
              onRetry={detail.reload}
            />
          </div>
        ) : (
          <CampaignReport detail={detail.data} />
        )}
      </StatSection>

      {compareIds.length >= 2 ? (
        <CampaignComparison
          ids={compareIds}
          onClose={() => setCompareIds([])}
        />
      ) : null}

      <StatSection
        title="All campaigns"
        aside={
          <div className="actions" style={{ margin: 0 }}>
            <button
              className="btn btn-secondary btn-sm"
              disabled={compareIds.length < 2}
              title={
                compareIds.length < 2
                  ? "Select at least two campaigns to compare"
                  : "Compare selected campaigns"
              }
              onClick={() => setCompareIds((prev) => (prev.length >= 2 ? [] : prev))}
            >
              {compareIds.length >= 2 ? `Compare ${compareIds.length} selected` : "Compare"}
            </button>
            <StatusFilter value={status} onChange={setStatus} />
          </div>
        }
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
                <th aria-label="Select for comparison">
                  <input
                    type="checkbox"
                    aria-label="Select all for comparison"
                    checked={rows.length > 0 && rows.every((c) => compareIds.includes(c.campaign_id))}
                    onChange={(e) => {
                      const next = e.target.checked ? rows.map((c) => c.campaign_id) : [];
                      setCompareIds((prev) => {
                        const keep = compareIds.filter(
                          (id) => !rows.some((c) => c.campaign_id === id),
                        );
                        return e.target.checked
                          ? [...new Set([...keep, ...next])]
                          : keep;
                      });
                    }}
                  />
                </th>
                <th>Campaign</th>
                <th>Status</th>
                <th>Period</th>
                <th>Targeted</th>
                <th>Plays</th>
                <th>Accepted</th>
                <th>Delivered</th>
                <th>Converted</th>
                <th>Conv. rate</th>
                <th>Avg time to convert</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((c) => {
                const presentation = campaignStatusPresentation(c.status);
                return (
                  <tr
                    key={c.campaign_id}
                    className={c.status === "active" ? "row-active" : undefined}
                  >
                    <td>
                      <input
                        type="checkbox"
                        aria-label="Select for comparison"
                        checked={compareIds.includes(c.campaign_id)}
                        onChange={() => toggleCompare(c.campaign_id)}
                      />
                    </td>
                    <td className="small">
                      <Link className="table-link" href={`/statistics/${c.campaign_id}`}>
                        {campaignDisplayName(c.name, c.campaign_id)}
                      </Link>
                    </td>
                    <td>
                      <StatusPill {...presentation} />
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
                    <td
                      className="small num"
                      title={`${c.activity.total_play_amount.toLocaleString()} amount`}
                    >
                      {formatNumber(c.activity.qualifying_plays)}
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