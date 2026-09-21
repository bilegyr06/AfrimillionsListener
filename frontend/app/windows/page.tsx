"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import StatusPill from "@/components/status-pill";
import { Empty, ErrorBlock, Loading } from "@/components/state-ui";
import { apiGet } from "@/lib/api";
import {
  formatDate,
  formatNumber,
  formatPercentage,
  formatRelativeTime,
  reportStatePresentation,
  windowDisplayName,
  windowStatusPresentation,
} from "@/lib/format";
import type { CampaignWindowRow } from "@/lib/types";
import { useQuery } from "@/lib/use-query";

// A split is always shown as both sides: "Campaign 80% / Control 20%". The
// percentages come straight from the backend's split summary.
function SplitCell({ row }: { row: CampaignWindowRow }) {
  const split = row.split;
  const actual = split.actual;
  if (actual.total_users <= 0) {
    return <span className="muted small">No audience yet</span>;
  }
  return (
    <div className="small">
      <div>
        Campaign {formatPercentage(actual.campaign_percentage)},
        Control {formatPercentage(actual.control_percentage)}
      </div>
      <div className="muted">
        {formatNumber(actual.campaign_users)} / {formatNumber(actual.control_users)}
      </div>
    </div>
  );
}

export default function WindowsPage() {
  const [nowTs, setNowTs] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNowTs(Date.now()), 60000);
    return () => clearInterval(id);
  }, []);

  const windows = useQuery<CampaignWindowRow[]>(() => apiGet("/windows"), [], 30000);

  return (
    <div className="page">
      <div className="page-head">
        <h1>Campaign windows</h1>
        <div className="actions">
          <button className="btn btn-secondary" onClick={windows.reload}>
            Refresh
          </button>
          <Link className="btn btn-primary" href="/windows/new">
            New window
          </Link>
        </div>
      </div>

      <section className="section">
        <div className="section-head">
          <h2>Windows</h2>
        </div>
        {windows.loading && !windows.data ? (
          <div className="section-body">
            <Loading text={"Loading campaign windows..."} />
          </div>
        ) : windows.error ? (
          <div className="section-body">
            <ErrorBlock message="We couldn't load campaign windows." onRetry={windows.reload} />
          </div>
        ) : !windows.data || windows.data.length === 0 ? (
          <div className="section-body">
            <Empty text="No campaign windows yet. Start one to configure a window and its runs." />
          </div>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Window</th>
                <th>Status</th>
                <th>Report</th>
                <th>Period</th>
                <th>Campaign / Control</th>
                <th>Runs</th>
                <th>Finalization</th>
              </tr>
            </thead>
            <tbody>
              {windows.data.map((w) => {
                const status = windowStatusPresentation(w.status);
                const report = reportStatePresentation(w.report_state);
                const now = new Date(nowTs);
                const deadlineText =
                  w.finalized_at
                    ? "Frozen"
                    : `${formatRelativeTime(w.finalization_deadline, now)} (${formatDate(w.finalization_deadline)})`;
                return (
                  <tr key={w.id} className={w.status === "active" ? "row-active" : undefined}>
                    <td className="small">
                      <Link className="table-link" href={`/windows/${w.id}`}>
                        {windowDisplayName(w.name, w.id)}
                      </Link>
                    </td>
                    <td>
                      <StatusPill {...status} />
                    </td>
                    <td>
                      <StatusPill {...report} />
                    </td>
                    <td className="small muted">
                      {formatDate(w.start_time)}
                      {"\u2009\u2192\u2009"}
                      {formatDate(w.end_time)}
                    </td>
                    <td>
                      <SplitCell row={w} />
                    </td>
                    <td className="small num">
                      {formatNumber(w.runs_count)}
                      {w.running_runs.length > 0 ? `${w.running_runs.length} running` : ""}
                    </td>
                    <td className="small muted">{deadlineText}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}