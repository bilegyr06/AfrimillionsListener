"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useMemo, useState } from "react";
import SnapshotDialog from "@/components/snapshot-dialog";
import StatusPill from "@/components/status-pill";
import { KvRows } from "@/components/statistics/kv";
import {
  Metric,
  MetricStrip,
  MetricTier,
  MetricTierSm,
} from "@/components/statistics/metric";
import StatSection from "@/components/statistics/section";
import { Empty, ErrorBlock, Loading } from "@/components/state-ui";
import { apiGet } from "@/lib/api";
import {
  campaignDisplayName,
  campaignStatusPresentation,
  formatDate,
  formatDateTime,
  formatDuration,
  formatMoney,
  formatNumber,
  formatPercent,
} from "@/lib/format";
import { buildStatisticsSnapshot, snapshotFilename } from "@/lib/snapshot";
import type { CampaignStatisticsDetail } from "@/lib/types";
import { useQuery } from "@/lib/use-query";

// Order of the distribution rows mirrors the backend's fixed bucket contract.
const BUCKET_ROWS: Array<[string, string]> = [
  ["lt_15m", "Under 15 minutes"],
  ["15m_to_1h", "15 minutes \u2013 1 hour"],
  ["1h_to_6h", "1 \u2013 6 hours"],
  ["6h_to_24h", "6 \u2013 24 hours"],
  ["24h_to_48h", "24 \u2013 48 hours"],
  ["ge_48h", "48 hours or more"],
];

const NOT_SENT_ROWS: Array<[string, string]> = [
  ["disqualified_played", "Removed \u2014 played before send"],
  ["skipped_cap", "Skipped \u2014 limit reached"],
  ["skipped_cooldown", "Skipped \u2014 cooldown active"],
  ["skipped_invalid_phone", "Skipped \u2014 no valid phone"],
  ["failed_send", "SMS failed to send"],
  ["expired", "Expired with campaign"],
];

export default function CampaignStatisticsPage() {
  const params = useParams<{ id: string }>();
  const campaignId = Number(params.id);
  const [snapshotOpen, setSnapshotOpen] = useState(false);

  const stats = useQuery<CampaignStatisticsDetail>(
    () => apiGet(`/stats/campaigns/${campaignId}`),
    [campaignId],
    30000,
  );

  const snapshot = useMemo(
    () => (stats.data ? buildStatisticsSnapshot(stats.data) : ""),
    [stats.data],
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

  const { campaign, window, audience, sms, response, economics } = stats.data;
  const status = campaignStatusPresentation(campaign.status);
  const timing = response.time_to_first_play;
  const attributionNote =
    campaign.status === "active"
      ? "Attribution window: SMS sent \u2192 now (rolling while active)"
      : `Attribution window: SMS sent \u2192 ${formatDateTime(window.attribution_end)}`;
  const notSentTotal = Object.values(audience.not_sent_to).reduce((sum, n) => sum + n, 0);

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <h1>{campaignDisplayName(campaign.name, campaign.id)}</h1>
            <StatusPill {...status} />
          </div>
          <p className="muted" style={{ margin: "4px 0 0", fontSize: 13 }}>
            {window.description}
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
          <button className="btn btn-secondary" onClick={() => setSnapshotOpen(true)}>
            Export report
          </button>
        </div>
      </div>

      <StatSection title="Campaign window" aside={<span className="muted small">{attributionNote}</span>}>
        <div className="section-body">
          <KvRows
            rows={[
              {
                label: "Period",
                value: `${formatDate(campaign.started_at)}\u2009\u2192\u2009${campaign.ended_at ? formatDate(campaign.ended_at) : "present"}`,
              },
              {
                label: "Attribution end",
                value: window.attribution_end ? formatDateTime(window.attribution_end) : "Now (rolling while active)",
              },
            ]}
          />
        </div>
      </StatSection>

      <StatSection title="Player response" aside={<span className="muted small">Within the attribution window</span>}>
        <div className="section-body flush">
          <MetricStrip>
            <Metric label="Customers contacted" value={formatNumber(response.contacted_customers)} />
            <Metric label="Converted customers" value={formatNumber(response.converted_customers)} accent />
            <Metric label="Conversion rate" value={formatPercent(response.conversion_rate)} accent />
          </MetricStrip>
          <MetricTier>
            <Metric label="Conversion events" value={formatNumber(response.conversion_events)} />
            <Metric label="Not converted" value={formatNumber(response.not_converted_customers)} />
            <Metric label="Still awaiting response" value={formatNumber(response.pending_outcome)} />
          </MetricTier>
        </div>
        <div className="section-body" style={{ paddingTop: 0 }}>
          <KvRows
            rows={[
              {
                label: "First qualifying play",
                value: response.first_qualifying_play_at ? formatDateTime(response.first_qualifying_play_at) : "\u2014",
              },
              { label: "Average time to first play", value: formatDuration(timing.avg) },
              { label: "Median time to first play", value: formatDuration(timing.median) },
              { label: "P25 \u2013 P75", value: `${formatDuration(timing.p25)} \u2013 ${formatDuration(timing.p75)}` },
              { label: "Min \u2013 max", value: `${formatDuration(timing.min)} \u2013 ${formatDuration(timing.max)}` },
            ]}
          />
          <p className="muted" style={{ margin: "10px 20px 0", fontSize: 12.5 }}>
            {response.qualifying_plays_note}
          </p>
        </div>
      </StatSection>

      <StatSection title="Time to first qualifying play" aside={<span className="muted small">From SMS sent to first play, per customer</span>}>
        <div className="section-body flush">
          <table className="table">
            <thead>
              <tr>
                <th>Bucket</th>
                <th className="num right">Customers</th>
              </tr>
            </thead>
            <tbody>
              {BUCKET_ROWS.map(([key, label]) => (
                <tr key={key}>
                  <td className="small">{label}</td>
                  <td className="small num right">{formatNumber(response.buckets[key as keyof typeof response.buckets] ?? 0)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </StatSection>

      <StatSection title="SMS performance" aside={<span className="muted small">Accepted Welcome SMS delivery funnel</span>}>
        <div className="section-body flush">
          <MetricStrip>
            <Metric label="Accepted (sent)" value={formatNumber(sms.accepted)} />
            <Metric label="Customers contacted" value={formatNumber(sms.contacted_customers)} />
            <Metric label="Delivered" value={formatNumber(sms.delivered)} accent />
          </MetricStrip>
          <MetricTierSm>
            <Metric label="Failed" value={formatNumber(sms.failed)} />
            <Metric label="Rejected" value={formatNumber(sms.rejected)} />
            <Metric label="Blocked (DND)" value={formatNumber(sms.dnd)} />
            <Metric label="Expired" value={formatNumber(sms.expired)} />
          </MetricTierSm>
        </div>
        <div className="section-body" style={{ paddingTop: 0 }}>
          <KvRows
            rows={[
              {
                label: "Delivery rate",
                value: `${formatPercent(sms.delivery_rate)} of accepted sends`,
              },
              {
                label: "Awaiting delivery",
                value: `${formatNumber(sms.sent_awaiting_delivery)}${sms.unmatched > 0 ? ` \u00b7 ${formatNumber(sms.unmatched)} unmatched to delivery log` : ""}`,
              },
            ]}
          />
        </div>
      </StatSection>

      <StatSection title="Audience" aside={<span className="muted small">Logins inside the campaign window</span>}>
        <div className="section-body flush">
          <MetricStrip>
            <Metric label="Opportunities" value={formatNumber(audience.opportunities)} />
            <Metric label="Unique customers" value={formatNumber(audience.unique_customers)} />
            <Metric label="Pending evaluation" value={formatNumber(audience.pending_evaluation)} />
          </MetricStrip>
        </div>
        <div className="section-body" style={{ paddingTop: 0 }}>
          <KvRows
            rows={[
              {
                label: "Not sent to",
                value: `${formatNumber(notSentTotal)} customers`,
              },
              ...NOT_SENT_ROWS.map(([key, label]) => ({
                label,
                value: formatNumber(
                  audience.not_sent_to[key as keyof typeof audience.not_sent_to],
                ),
              })),
            ]}
          />
        </div>
      </StatSection>

      <StatSection title="Game activity" aside={<span className="muted small">Coming in phase 2</span>}>
        <div className="section-body">
          <Empty
            text="Game-level and repeat-play breakdowns need play-level persistence (Phase 2). Play data is available locally but not yet processed into the database."
          />
        </div>
      </StatSection>

      <StatSection title="Economics" aside={<span className="muted small">SMS cost only</span>}>
        <div className="section-body">
          <KvRows
            rows={[
              { label: "SMS cost", value: formatMoney(economics.sms_cost) },
              { label: "Avg cost per accepted SMS", value: formatMoney(economics.avg_cost_per_accepted) },
            ]}
          />
        </div>
      </StatSection>

      <SnapshotDialog
        open={snapshotOpen}
        title="Campaign statistics report"
        text={snapshot}
        filename={snapshotFilename(`statistics-campaign-${campaign.id}`)}
        onClose={() => setSnapshotOpen(false)}
      />
    </div>
  );
}