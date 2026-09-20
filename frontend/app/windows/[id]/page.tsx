"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import StatusPill from "@/components/status-pill";
import { Metric, MetricStrip, MetricTier } from "@/components/statistics/metric";
import StatSection from "@/components/statistics/section";
import { ErrorBlock, Loading } from "@/components/state-ui";
import { apiGet } from "@/lib/api";
import {
  formatDateTime,
  formatMoney,
  formatNumber,
  formatPercent,
  formatRatio,
  runStatusPresentation,
  windowDisplayName,
  windowStatusPresentation,
} from "@/lib/format";
import type { WindowGroupMetrics, WindowReport } from "@/lib/types";
import { useQuery } from "@/lib/use-query";

function GroupMetrics({ group, title }: { group: WindowGroupMetrics; title: string }) {
  const pct = (v: number | null | undefined) => formatPercent(v);
  return (
    <StatSection title={title}>
      <div className="section-body flush">
        <MetricStrip>
          <Metric label="Audience" value={formatNumber(group.total_targeted_audience)} />
          <Metric label="Played" value={formatNumber(group.total_played_users)} />
          <Metric label="Deposited" value={formatNumber(group.total_deposited_users)} />
          <Metric label="Logged in" value={formatNumber(group.total_logged_in_users)} />
        </MetricStrip>
        <MetricTier>
          <Metric label="Total sales" value={formatMoney(group.total_sales)} accent />
          <Metric label="Total plays" value={formatNumber(group.total_plays)} />
          <Metric label="ARPU" value={formatMoney(group.arpu)} />
          <Metric label="ARPPU" value={formatMoney(group.arppu)} />
        </MetricTier>
      </div>
      <div className="section-body">
        <table className="kv">
          <tbody>
            <tr>
              <th>Login rate</th>
              <td>{pct(group.login_rate)}</td>
            </tr>
            <tr>
              <th>Play rate</th>
              <td>{pct(group.play_rate)}</td>
            </tr>
            <tr>
              <th>Deposit rate</th>
              <td>{pct(group.deposit_rate)}</td>
            </tr>
            <tr>
              <th>Plays per player</th>
              <td>{formatRatio(group.plays_per_player)}</td>
            </tr>
            <tr>
              <th>Active days per player</th>
              <td>{formatRatio(group.active_days_per_player)}</td>
            </tr>
            <tr>
              <th>Multi-day players</th>
              <td>{formatNumber(group.multi_day_players)}</td>
            </tr>
            <tr>
              <th>Multi-day player rate</th>
              <td>{pct(group.multi_day_player_rate)}</td>
            </tr>
            <tr>
              <th>Deposit-to-play rate</th>
              <td>{pct(group.deposit_to_play_rate)}</td>
            </tr>
            <tr>
              <th>Login-to-play rate</th>
              <td>{pct(group.login_to_play_rate)}</td>
            </tr>
            <tr>
              <th>Deposited, no play</th>
              <td>{formatNumber(group.deposited_no_play_users)}</td>
            </tr>
            <tr>
              <th>Logged in, no play</th>
              <td>{formatNumber(group.logged_in_no_play_users)}</td>
            </tr>
            <tr>
              <th>Single-play players</th>
              <td>{formatNumber(group.single_play_players)}</td>
            </tr>
            <tr>
              <th>Multiple-play players</th>
              <td>{formatNumber(group.multiple_play_players)}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </StatSection>
  );
}

function CampaignAttribution({ group }: { group: WindowGroupMetrics }) {
  if (group.conversion_not_applicable) {
    return (
      <StatSection title="Campaign SMS attribution">
        <div className="section-body">
          <p className="muted" style={{ margin: 0 }}>
            Control users never receive Campaign SMS and have no Campaign
            conversion model; these metrics are not applicable to the Control
            group.
          </p>
        </div>
      </StatSection>
    );
  }
  return (
    <StatSection title="Campaign SMS attribution">
      <div className="section-body flush">
        <MetricStrip>
          <Metric label="Contacted" value={formatNumber(group.contacted_users)} accent />
          <Metric label="Converted" value={formatNumber(group.converted_users)} accent />
        </MetricStrip>
        <MetricTier>
          <Metric label="Conversion rate" value={formatPercent(group.conversion_rate)} />
          <Metric label="Plays / converted" value={formatRatio(group.avg_plays_per_converted)} />
          <Metric label="Plays / contacted" value={formatRatio(group.avg_plays_per_contacted)} />
          <Metric label="Amount / converted" value={formatMoney(group.avg_amount_per_converted)} />
        </MetricTier>
      </div>
    </StatSection>
  );
}

function RunTable({ report }: { report: WindowReport }) {
  return (
    <StatSection title="Runs">
      <div className="section-body flush">
        <table className="table">
          <thead>
            <tr>
              <th>Run</th>
              <th>Status</th>
              <th>Target</th>
              <th>Accepted</th>
              <th>Contacted</th>
              <th>Converted</th>
              <th>Attributed plays</th>
              <th>Attributed amount</th>
            </tr>
          </thead>
          <tbody>
            {report.runs.map((r) => {
              const status = runStatusPresentation(r.status);
              return (
                <tr key={r.run_id}>
                  <td className="small">Run #{r.run_id}</td>
                  <td>
                    <StatusPill {...status} />
                  </td>
                  <td className="small num">{formatNumber(r.targeted_users)}</td>
                  <td className="small num">{formatNumber(r.accepted_interventions)}</td>
                  <td className="small num">{formatNumber(r.contacted_users)}</td>
                  <td className="small num">{formatNumber(r.converted_users)}</td>
                  <td className="small num">{formatNumber(r.attributed_plays)}</td>
                  <td className="small num">{formatMoney(r.attributed_amount)}</td>
                </tr>
              );
            })}
            {report.runs.length === 0 ? (
              <tr>
                <td colSpan={8} className="small muted">
                  No Campaign Runs started for this window.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
    </StatSection>
  );
}

export default function WindowReportPage() {
  const params = useParams<{ id: string }>();
  const windowId = Number(params.id);

  const report = useQuery<WindowReport>(() => apiGet(`/windows/${windowId}/report`), [windowId], 30000);

  if (report.loading && !report.data) {
    return (
      <div className="page">
        <div className="page-head">
          <h1>Window report</h1>
        </div>
        <div className="section">
          <Loading text="Loading window report\u2026" />
        </div>
      </div>
    );
  }

  if (report.error || !report.data) {
    return (
      <div className="page">
        <div className="page-head">
          <h1>Window report</h1>
          <Link className="btn btn-secondary" href="/windows">
            Back to windows
          </Link>
        </div>
        <div className="section">
          <div className="section-body">
            <ErrorBlock message="We couldn't load this window's report." onRetry={report.reload} />
          </div>
        </div>
      </div>
    );
  }

  const data = report.data;
  const windowStatus = windowStatusPresentation(data.window.status);

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <h1>{windowDisplayName(data.window.name, data.window.id)}</h1>
            <StatusPill {...windowStatus} />
          </div>
          <p className="muted" style={{ margin: "4px 0 0", fontSize: 13 }}>
            {data.evaluation_period.description}
          </p>
        </div>
        <div className="actions">
          <Link className="btn btn-secondary" href="/windows">
            Back to windows
          </Link>
          <button className="btn btn-secondary" onClick={report.reload}>
            Refresh
          </button>
        </div>
      </div>

      <StatSection title="Window">
        <div className="section-body">
          <table className="kv">
            <tbody>
              <tr>
                <th>Period</th>
                <td>
                  {formatDateTime(data.evaluation_period.start)}
                  {"\u2009\u2192\u2009"}
                  {formatDateTime(data.evaluation_period.end)}
                </td>
              </tr>
              <tr>
                <th>Status</th>
                <td>{data.window.status}</td>
              </tr>
              <tr>
                <th>Finalization</th>
                <td>
                  {data.window.finalized_at
                    ? formatDateTime(data.window.finalized_at)
                    : `Deadline ${formatDateTime(data.window.finalization_deadline)}`}
                </td>
              </tr>
              <tr>
                <th>Time zone</th>
                <td>{data.evaluation_period.timezone}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div className="section-body flush">
          <MetricStrip>
            <Metric label="Campaignable" value={formatNumber(data.audience.campaignable_total)} />
            <Metric label="Campaign" value={formatNumber(data.audience.campaign)} />
            <Metric label="Control" value={formatNumber(data.audience.control)} />
          </MetricStrip>
        </div>
        <div className="section-body flush">
          <MetricStrip>
            <Metric
              label="Attributed plays"
              value={formatNumber(data.attribution.attributed_plays)}
            />
            <Metric
              label="Attributed amount"
              value={formatMoney(data.attribution.attributed_amount)}
            />
            <Metric
              label="Unattributed plays"
              value={formatNumber(data.attribution.unattributed_plays)}
            />
          </MetricStrip>
        </div>
      </StatSection>

      <CampaignAttribution group={data.groups.campaign} />

      <GroupMetrics group={data.groups.campaign} title="Campaign group" />
      <GroupMetrics group={data.groups.control} title="Control group" />

      <StatSection title="Games">
        {["campaign", "control"].map((kind) => {
          const games = data.games[kind as keyof WindowReport["games"]];
          return (
            <div className="section-body flush" key={`games-${kind}`}>
              <h3 style={{ margin: "0 20px 8px", fontSize: 13, textTransform: "capitalize" }}>
                {kind} group
              </h3>
              <table className="table">
                <thead>
                  <tr>
                    <th>Game</th>
                    <th>Plays</th>
                    <th>Customers</th>
                    <th>Amount</th>
                    <th>Avg amount</th>
                  </tr>
                </thead>
                <tbody>
                  {games.map((g) => (
                    <tr key={g.game_name}>
                      <td className="small">{g.game_name}</td>
                      <td className="small num">{formatNumber(g.plays)}</td>
                      <td className="small num">{formatNumber(g.customers)}</td>
                      <td className="small num">{formatMoney(g.amount)}</td>
                      <td className="small num">{formatMoney(g.avg_amount)}</td>
                    </tr>
                  ))}
                  {games.length === 0 ? (
                    <tr>
                      <td colSpan={5} className="small muted">
                        No plays in this group.
                      </td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </div>
          );
        })}
      </StatSection>

      <RunTable report={data} />
    </div>
  );
}