"use client";

import Link from "next/link";
import StatusPill from "@/components/status-pill";
import { Empty, ErrorBlock, Loading } from "@/components/state-ui";
import { apiGet } from "@/lib/api";
import { formatDate, formatNumber, windowDisplayName, windowStatusPresentation } from "@/lib/format";
import type { CampaignWindowRow } from "@/lib/types";
import { useQuery } from "@/lib/use-query";

export default function WindowsPage() {
  const windows = useQuery<CampaignWindowRow[]>(() => apiGet("/windows"), [], 30000);

  return (
    <div className="page">
      <div className="page-head">
        <h1>Campaign windows</h1>
        <div className="actions">
          <button className="btn btn-secondary" onClick={windows.reload}>
            Refresh
          </button>
        </div>
      </div>

      <section className="section">
        <div className="section-head">
          <h2>Windows</h2>
        </div>
        {windows.loading && !windows.data ? (
          <div className="section-body">
            <Loading text="Loading campaign windows\u2026" />
          </div>
        ) : windows.error ? (
          <div className="section-body">
            <ErrorBlock message="We couldn't load campaign windows." onRetry={windows.reload} />
          </div>
        ) : !windows.data || windows.data.length === 0 ? (
          <div className="section-body">
            <Empty text="No campaign windows yet." />
          </div>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Window</th>
                <th>Status</th>
                <th>Period</th>
                <th>Finalization deadline</th>
                <th>Eligible (N)</th>
                <th>Control %</th>
              </tr>
            </thead>
            <tbody>
              {windows.data.map((w) => {
                const presentation = windowStatusPresentation(w.status);
                return (
                  <tr key={w.id} className={w.status === "active" ? "row-active" : undefined}>
                    <td className="small">
                      <Link className="table-link" href={`/windows/${w.id}`}>
                        {windowDisplayName(w.name, w.id)}
                      </Link>
                    </td>
                    <td>
                      <StatusPill {...presentation} />
                    </td>
                    <td className="small muted">
                      {formatDate(w.start_time)}
                      {"\u2009\u2192\u2009"}
                      {formatDate(w.end_time)}
                    </td>
                    <td className="small muted">{formatDate(w.finalization_deadline)}</td>
                    <td className="small num">{formatNumber(w.eligible_count)}</td>
                    <td className="small num">{w.control_percentage ?? "\u2014"}</td>
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