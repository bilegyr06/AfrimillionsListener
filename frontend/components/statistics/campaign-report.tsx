// Full statistics report for one campaign. Shared by the overview page (which
// renders it for the selected campaign) and the per-campaign page, so both
// surfaces present the same sections, numbers, and export.

"use client";

import { useMemo, useState } from "react";
import SnapshotDialog from "@/components/snapshot-dialog";
import { KvRows } from "@/components/statistics/kv";
import {
  Metric,
  MetricStrip,
  MetricTier,
  MetricTierSm,
} from "@/components/statistics/metric";
import StatSection from "@/components/statistics/section";
import { Empty } from "@/components/state-ui";
import {
  formatDateTime,
  formatDuration,
  formatMoney,
  formatNumber,
  formatPercent,
  formatRatio,
} from "@/lib/format";
import { buildStatisticsSnapshot, snapshotFilename } from "@/lib/snapshot";
import type { CampaignStatisticsDetail } from "@/lib/types";

// Order of the distribution rows mirrors the backend's fixed bucket contract.
const BUCKET_ROWS: Array<[string, string]> = [
  ["lt_1h", "Under 1 hour"],
  ["1h_to_6h", "1 \u2013 6 hours"],
  ["6h_to_12h", "6 \u2013 12 hours"],
  ["12h_to_24h", "12 \u2013 24 hours"],
  ["ge_24h", "24 hours or more"],
];

const NOT_SENT_ROWS: Array<[string, string]> = [
  ["disqualified_played", "Removed \u2014 played before send"],
  ["skipped_cap", "Skipped \u2014 limit reached"],
  ["skipped_cooldown", "Skipped \u2014 cooldown active"],
  ["skipped_invalid_phone", "Skipped \u2014 no valid phone"],
  ["failed_send", "SMS failed to send"],
  ["expired", "Expired with campaign"],
];

export default function CampaignReport({ detail }: { detail: CampaignStatisticsDetail }) {
  const [snapshotOpen, setSnapshotOpen] = useState(false);
  const { campaign, window, audience, funnel, sms, response, activity, games, economics } =
    detail;
  const timing = response.time_to_first_play;
  const attributionNote =
    campaign.status === "active"
      ? "Attribution window: SMS sent \u2192 now (rolling while active)"
      : `Attribution window: SMS sent \u2192 ${formatDateTime(window.attribution_end)}`;
  const notSentTotal = Object.values(audience.not_sent_to).reduce((sum, n) => sum + n, 0);

  const snapshot = useMemo(() => buildStatisticsSnapshot(detail), [detail]);

  return (
    <>
      <StatSection
        title="Campaign window"
        aside={
          <div className="actions" style={{ margin: 0 }}>
            <button className="btn btn-secondary btn-sm" onClick={() => setSnapshotOpen(true)}>
              Export report
            </button>
          </div>
        }
      >
        <div className="section-body">
          <KvRows
            rows={[
              {
                label: "Period",
                value: `${formatDateTime(campaign.started_at)}\u2009\u2192\u2009${campaign.ended_at ? formatDateTime(campaign.ended_at) : "present"}`,
              },
              {
                label: "Attribution",
                value: attributionNote,
              },
              {
                label: "Attribution end",
                value: window.attribution_end ? formatDateTime(window.attribution_end) : "Now (rolling while active)",
              },
            ]}
          />
        </div>
      </StatSection>

      <StatSection
        title="Campaign funnel"
        aside={<span className="muted small">Logins \u2192 customers \u2192 sends \u2192 delivery \u2192 conversions</span>}
      >
        <div className="section-body flush">
          <MetricStrip>
            <Metric label="Opportunities" value={formatNumber(funnel.opportunities)} />
            <Metric label="Unique customers" value={formatNumber(funnel.unique_customers)} />
            <Metric label="Accepted (sent)" value={formatNumber(funnel.accepted)} />
          </MetricStrip>
          <MetricTier>
            <Metric label="Delivered" value={formatNumber(funnel.delivered)} accent />
            <Metric label="Converted customers" value={formatNumber(funnel.converted_customers)} accent />
            <Metric label="Conversion rate" value={formatPercent(response.conversion_rate)} />
          </MetricTier>
        </div>
        <div className="section-body" style={{ paddingTop: 0 }}>
          <p className="muted" style={{ margin: "10px 20px 0", fontSize: 12.5 }}>
            Stages move from login events to distinct customers, then to accepted
            SMS sends, delivery outcomes, and finally to converted customers. A
            customer can appear in more than one stage, so the funnel is not a set
            of strict one-to-one drops.
          </p>
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
            <Metric label="Deferred" value={formatNumber(sms.deferred)} />
            <Metric label="Expired" value={formatNumber(sms.expired)} />
          </MetricTierSm>
        </div>
        <div className="section-body" style={{ paddingTop: 0 }}>
          <KvRows
            rows={[
              {
                label: "Delivery rate",
                value:
                  sms.delivery_rate == null
                    ? "No accepted sends to measure"
                    : `${formatPercent(sms.delivery_rate)} of accepted sends`,
              },
              {
                label: "Awaiting delivery",
                value: `${formatNumber(sms.sent_awaiting_delivery)}${sms.unmatched > 0 ? ` \u00b7 ${formatNumber(sms.unmatched)} unmatched to delivery log` : ""}`,
              },
            ]}
          />
          <p className="muted" style={{ margin: "10px 20px 0", fontSize: 12.5 }}>
            Deferred means the send was accepted but no provider call was made;
            those sends never produce a delivery confirmation.
          </p>
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

      <StatSection title="Player activity" aside={<span className="muted small">Qualifying plays by contacted customers</span>}>
        <div className="section-body flush">
          <MetricStrip>
            <Metric label="Qualifying plays" value={formatNumber(activity.qualifying_plays)} />
            <Metric label="Players" value={formatNumber(activity.players)} accent />
            <Metric label="Repeat players" value={formatNumber(activity.repeat_players)} accent />
          </MetricStrip>
          <MetricTier>
            <Metric label="Converted players" value={formatNumber(activity.converted_players)} />
            <Metric label="Played before SMS" value={formatNumber(activity.before_sms)} />
            <Metric label="Played after window" value={formatNumber(activity.after_window)} />
          </MetricTier>
        </div>
        <div className="section-body" style={{ paddingTop: 0 }}>
          <KvRows
            rows={[
              { label: "Average plays per player", value: formatRatio(activity.avg_plays_per_player) },
              { label: "Repeat share", value: formatPercent(activity.repeat_rate) },
              { label: "Most plays by one player", value: formatNumber(activity.max_plays_per_player) },
              { label: "Total play amount", value: formatMoney(activity.total_play_amount) },
              { label: "Average amount per play", value: formatMoney(activity.avg_play_amount) },
            ]}
          />
          <p className="muted" style={{ margin: "10px 20px 0", fontSize: 12.5 }}>
            A qualifying play lies strictly after the customer\u2019s first Welcome
            SMS and at or before the attribution window end. Plays before the SMS
            would have disqualified that customer\u2019s send, so they are listed
            separately above.
          </p>
        </div>
      </StatSection>

      <StatSection title="Game activity" aside={<span className="muted small">Qualifying plays by game</span>}>
        <div className="section-body flush">
          {games.length === 0 ? (
            <Empty text="No qualifying plays to rank." />
          ) : (
            <table className="table">
              <thead>
                <tr>
                  <th>Game</th>
                  <th className="num right">Plays</th>
                  <th className="num right">Players</th>
                  <th className="num right">Amount</th>
                </tr>
              </thead>
              <tbody>
                {games.map((game) => (
                  <tr key={game.game_name}>
                    <td className="small">{game.game_name}</td>
                    <td className="small num right">{formatNumber(game.plays)}</td>
                    <td className="small num right">{formatNumber(game.customers)}</td>
                    <td className="small num right">{formatMoney(game.amount)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
        <div className="section-body" style={{ paddingTop: 0 }}>
          <p className="muted" style={{ margin: "10px 20px 0", fontSize: 12.5 }}>
            Ranked by plays, then by play amount. Customers of a game are the
            distinct players who qualify in the window, not unique to one game.
          </p>
        </div>
      </StatSection>

      <StatSection title="Campaign cost" aside={<span className="muted small">SMS cost only</span>}>
        <div className="section-body">
          <KvRows
            rows={[
              { label: "SMS cost", value: formatMoney(economics.sms_cost) },
              { label: "Avg cost per accepted SMS", value: formatMoney(economics.avg_cost_per_accepted) },
              { label: "Cost per contacted customer", value: formatMoney(economics.cost_per_contacted) },
              { label: "Cost per conversion", value: formatMoney(economics.cost_per_conversion) },
            ]}
          />
          <p className="muted" style={{ margin: "10px 20px 0", fontSize: 12.5 }}>
            A \u201c\u2014\u201d cost-per row means there is no customer column to divide over,
            not a zero cost.
          </p>
        </div>
      </StatSection>

      <SnapshotDialog
        open={snapshotOpen}
        title="Campaign statistics report"
        text={snapshot}
        filename={snapshotFilename(`statistics-campaign-${campaign.id}`)}
        onClose={() => setSnapshotOpen(false)}
      />
    </>
  );
}