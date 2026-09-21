// Full statistics report for one campaign. Shared by the overview page (which
// renders it for the selected campaign) and the per-campaign page, so both
// surfaces present the same sections, numbers, and export.
//
// The information architecture follows the campaign chain from left to right:
// Campaign overview -> Customer response -> Player activity -> Game activity ->
// SMS performance -> Campaign economics. "Economics" never claims profitability:
// it reports the sms cost and the attributed play amount, and the
// activity/cost ratio is a descriptive ratio of available data, not ROI.

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
import {
  buildCampaignReport,
  NOT_SENT_ROWS,
  RESPONSE_BUCKET_ROWS,
} from "@/lib/campaign-report";
import type { CampaignStatisticsDetail } from "@/lib/types";

export default function CampaignReport({ detail }: { detail: CampaignStatisticsDetail }) {
  const [snapshotOpen, setSnapshotOpen] = useState(false);
  const r = buildCampaignReport(detail);

  const snapshot = useMemo(() => buildStatisticsSnapshot(detail), [detail]);

  return (
    <>
      <StatSection
        title="Campaign overview"
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
                value: `${formatDateTime(r.campaign.started_at)}\u2009\u2192\u2009${r.campaign.ended_at ? formatDateTime(r.campaign.ended_at) : "present"}`,
              },
              { label: "Status", value: r.campaign.status },
              { label: "Attribution", value: r.attributionNote },
              {
                label: "Attribution end",
                value: r.window.attribution_end ? formatDateTime(r.window.attribution_end) : "Now (rolling while active)",
              },
            ]}
          />
        </div>
        <div className="section-body flush">
          <MetricStrip>
            <Metric label="Opportunities" value={formatNumber(r.funnel.opportunities)} />
            <Metric label="Unique customers" value={formatNumber(r.funnel.unique_customers)} />
            <Metric label="Accepted (sent)" value={formatNumber(r.funnel.accepted)} />
          </MetricStrip>
          <MetricTier>
            <Metric label="Delivered" value={formatNumber(r.funnel.delivered)} accent />
            <Metric label="Converted customers" value={formatNumber(r.funnel.converted_customers)} accent />
            <Metric label="Conversion rate" value={formatPercent(r.response.conversion_rate)} />
          </MetricTier>
        </div>
        <div className="section-body flush" style={{ paddingTop: 0 }}>
          <MetricStrip>
            <Metric label="Pending evaluation" value={formatNumber(r.audience.pending_evaluation)} />
            <Metric label="Not sent to" value={formatNumber(r.notSentTotal)} />
          </MetricStrip>
        </div>
        <div className="section-body" style={{ paddingTop: 0 }}>
          <KvRows
            rows={NOT_SENT_ROWS.map(([key, label]) => ({
              label,
              value: formatNumber(r.audience.not_sent_to[key]),
            }))}
          />
          <p className="muted" style={{ margin: "10px 20px 0", fontSize: 12.5 }}>
            The funnel moves from login events to distinct customers, then to
            accepted SMS sends, delivery outcomes, and finally to converted
            customers. A customer can appear in more than one stage, so the
            funnel is not a set of strict one-to-one drops.
          </p>
        </div>
      </StatSection>

      <StatSection title="Customer response" aside={<span className="muted small">Within the attribution window</span>}>
        <div className="section-body flush">
          <MetricStrip>
            <Metric label="Customers contacted" value={formatNumber(r.response.contacted_customers)} />
            <Metric label="Converted customers" value={formatNumber(r.response.converted_customers)} accent />
            <Metric label="Conversion rate" value={formatPercent(r.response.conversion_rate)} accent />
          </MetricStrip>
          <MetricTier>
            <Metric label="Conversion events" value={formatNumber(r.response.conversion_events)} />
            <Metric label="Not converted" value={formatNumber(r.response.not_converted_customers)} />
            <Metric label="Still awaiting response" value={formatNumber(r.response.pending_outcome)} />
          </MetricTier>
        </div>
        <div className="section-body" style={{ paddingTop: 0 }}>
          <KvRows
            rows={[
              {
                label: "First qualifying play",
                value: r.response.first_qualifying_play_at ? formatDateTime(r.response.first_qualifying_play_at) : "\u2014",
              },
              { label: "Average time to first play", value: formatDuration(r.response.time_to_first_play.avg) },
              { label: "Median time to first play", value: formatDuration(r.response.time_to_first_play.median) },
              { label: "P25 \u2013 P75", value: `${formatDuration(r.response.time_to_first_play.p25)} \u2013 ${formatDuration(r.response.time_to_first_play.p75)}` },
              { label: "Min \u2013 max", value: `${formatDuration(r.response.time_to_first_play.min)} \u2013 ${formatDuration(r.response.time_to_first_play.max)}` },
            ]}
          />
        </div>
        <div className="section-body flush" style={{ paddingTop: 0 }}>
          <table className="table">
            <thead>
              <tr>
                <th>Time to first qualifying play</th>
                <th className="num right">Customers</th>
              </tr>
            </thead>
            <tbody>
              {RESPONSE_BUCKET_ROWS.map(([key, label]) => (
                <tr key={key}>
                  <td className="small">{label}</td>
                  <td className="small num right">{formatNumber(r.response.buckets[key] ?? 0)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </StatSection>

      <StatSection title="Player activity" aside={<span className="muted small">Qualifying plays by contacted customers</span>}>
        <div className="section-body flush">
          <MetricStrip>
            <Metric label="Qualifying plays" value={formatNumber(r.activity.qualifying_plays)} />
            <Metric label="Players" value={formatNumber(r.activity.players)} accent />
            <Metric label="Repeat players" value={formatNumber(r.activity.repeat_players)} accent />
          </MetricStrip>
          <MetricTier>
            <Metric label="Converted players" value={formatNumber(r.activity.converted_players)} />
            <Metric label="Played before SMS" value={formatNumber(r.activity.before_sms)} />
            <Metric label="Played after window" value={formatNumber(r.activity.after_window)} />
          </MetricTier>
        </div>
        <div className="section-body" style={{ paddingTop: 0 }}>
          <KvRows
            rows={[
              { label: "Games played", value: formatNumber(r.activity.game_count) },
              { label: "Played once", value: formatNumber(r.activity.single_play_players) },
              { label: "Average plays per player", value: formatRatio(r.activity.avg_plays_per_player) },
              { label: "Average plays per converted customer", value: formatRatio(r.activity.avg_plays_per_converted) },
              { label: "Average plays per contacted customer", value: formatRatio(r.activity.avg_plays_per_contacted) },
              { label: "Repeat share", value: formatPercent(r.activity.repeat_rate) },
              { label: "Most plays by one player", value: formatNumber(r.activity.max_plays_per_player) },
              { label: "Total play amount", value: formatMoney(r.activity.total_play_amount) },
              { label: "Average amount per play", value: formatMoney(r.activity.avg_play_amount) },
              { label: "Average amount per converted customer", value: formatMoney(r.activity.avg_amount_per_converted) },
              { label: "Average amount per contacted customer", value: formatMoney(r.activity.avg_amount_per_contacted) },
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
          {r.games.length === 0 ? (
            <Empty text="No qualifying plays to rank." />
          ) : (
            <table className="table">
              <thead>
                <tr>
                  <th>Game</th>
                  <th className="num right">Plays</th>
                  <th className="num right">Players</th>
                  <th className="num right">Amount</th>
                  <th className="num right">Avg. per play</th>
                </tr>
              </thead>
              <tbody>
                {r.games.map((game) => (
                  <tr key={game.game_name}>
                    <td className="small">{game.game_name}</td>
                    <td className="small num right">{formatNumber(game.plays)}</td>
                    <td className="small num right">{formatNumber(game.customers)}</td>
                    <td className="small num right">{formatMoney(game.amount)}</td>
                    <td className="small num right">{formatMoney(game.avg_amount)}</td>
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

      <StatSection title="SMS performance" aside={<span className="muted small">Accepted Welcome SMS delivery funnel</span>}>
        <div className="section-body flush">
          <MetricStrip>
            <Metric label="Accepted (sent)" value={formatNumber(r.sms.accepted)} />
            <Metric label="Customers contacted" value={formatNumber(r.sms.contacted_customers)} />
            <Metric label="Delivered" value={formatNumber(r.sms.delivered)} accent />
          </MetricStrip>
          <MetricTierSm>
            <Metric label="Failed" value={formatNumber(r.sms.failed)} />
            <Metric label="Rejected" value={formatNumber(r.sms.rejected)} />
            <Metric label="Blocked (DND)" value={formatNumber(r.sms.dnd)} />
            <Metric label="Deferred" value={formatNumber(r.sms.deferred)} />
            <Metric label="Expired" value={formatNumber(r.sms.expired)} />
          </MetricTierSm>
        </div>
        <div className="section-body" style={{ paddingTop: 0 }}>
          <KvRows
            rows={[
              {
                label: "Delivery rate",
                value:
                  r.sms.delivery_rate == null
                    ? "No accepted sends to measure"
                    : `${formatPercent(r.sms.delivery_rate)} of accepted sends`,
              },
              {
                label: "Awaiting delivery",
                value: `${formatNumber(r.sms.sent_awaiting_delivery)}${r.sms.unmatched > 0 ? `${formatNumber(r.sms.unmatched)} unmatched to delivery log` : ""}`,
              },
              {
                label: "Avg cost per accepted SMS",
                value: formatMoney(r.economics.avg_cost_per_accepted),
              },
            ]}
          />
          <p className="muted" style={{ margin: "10px 20px 0", fontSize: 12.5 }}>
            Deferred means the send was accepted but no provider call was made;
            those sends never produce a delivery confirmation.
          </p>
        </div>
      </StatSection>

      <StatSection title="Campaign economics" aside={<span className="muted small">SMS cost vs attributed play amount</span>}>
        <div className="section-body">
          <KvRows
            rows={[
              { label: "SMS cost", value: formatMoney(r.economics.sms_cost) },
              { label: "Cost per contacted customer", value: formatMoney(r.economics.cost_per_contacted) },
              { label: "Cost per conversion", value: formatMoney(r.economics.cost_per_conversion) },
              { label: "Attributed play amount", value: formatMoney(r.economics.total_play_amount) },
              { label: "Play amount per converted customer", value: formatMoney(r.economics.play_amount_per_converted) },
              { label: "Play amount per contacted customer", value: formatMoney(r.economics.play_amount_per_contacted) },
              { label: "Activity / cost ratio", value: formatRatio(r.economics.activity_cost_ratio) },
            ]}
          />
          <p className="muted" style={{ margin: "10px 20px 0", fontSize: 12.5 }}>
            SMS cost covers this campaign\u2019s own accepted Welcome SMS. A
            \u201c\u2014\u201d on a per-customer row means there is no customer column to
            divide over, not a zero cost. Attributed play amount is the qualifying
            plays inside the attribution window \u2014 it is not revenue, and the
            activity/cost ratio is a descriptive ratio of available data, not ROI.
          </p>
        </div>
      </StatSection>

      <SnapshotDialog
        open={snapshotOpen}
        title="Campaign statistics report"
        text={snapshot}
        filename={snapshotFilename(`statistics-campaign-${r.campaign.id}`)}
        onClose={() => setSnapshotOpen(false)}
      />
    </>
  );
}