"use client";

import { useEffect, useMemo, useState } from "react";
import Pager from "@/components/pager";
import StatusPill from "@/components/status-pill";
import { Empty, ErrorBlock, Loading } from "@/components/state-ui";
import { apiGet } from "@/lib/api";
import { formatDateTime, formatMoney, formatNumber, smsKindLabel, smsStatusPresentation } from "@/lib/format";
import type { SmsLogsResponse } from "@/lib/types";
import { useQuery } from "@/lib/use-query";

export default function SmsPage() {
  const [page, setPage] = useState(1);
  const [kind, setKind] = useState("");
  const [since, setSince] = useState("");
  const [until, setUntil] = useState("");

  const logs = useQuery<SmsLogsResponse>(
    () => apiGet("/sms/logs", { page, page_size: 50, kind, since, until }),
    [page, kind, since, until],
    15000,
  );

  // Build the kind filter from the backend's active scope so the UI never
  // re-derives which features are enabled.  Manual always appears because it
  // is an operator action, not a feature toggle.
  const enabledFeatures = logs.data?.enabled_features;
  const kindOptions = useMemo(
    () => [
      { value: "", label: "All types" },
      ...(enabledFeatures ?? []).map((f) => ({ value: f, label: smsKindLabel(f) })),
      { value: "manual", label: smsKindLabel("manual") },
    ],
    [enabledFeatures],
  );

  // If a previously-selected kind is no longer offered (feature toggled off),
  // clear the filter rather than leaving a stale selection in the dropdown.
  useEffect(() => {
    if (kind && !kindOptions.some((o) => o.value === kind)) {
      setKind("");
      setPage(1);
    }
  }, [kind, kindOptions]);

  return (
    <div className="page">
      <div className="page-head">
        <h1>SMS activity</h1>
        <div className="actions">
          <button className="btn btn-secondary" onClick={logs.reload}>
            Refresh
          </button>
        </div>
      </div>

      <section className="section">
        <div className="section-head filter-bar">
          <label>
            Type
            <select value={kind} onChange={(e) => { setKind(e.target.value); setPage(1); }}>
              {kindOptions.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
          </label>
          <label>
            From
            <input type="date" value={since} onChange={(e) => { setSince(e.target.value); setPage(1); }} />
          </label>
          <label>
            To
            <input type="date" value={until} onChange={(e) => { setUntil(e.target.value); setPage(1); }} />
          </label>
          {(kind || since || until) && (
            <button
              className="btn btn-ghost"
              onClick={() => {
                setKind("");
                setSince("");
                setUntil("");
                setPage(1);
              }}
            >
              Clear filters
            </button>
          )}
        </div>
        <div className="section-body flush">
          {logs.loading && !logs.data ? (
            <Loading text="Loading SMS activity\u2026" />
          ) : logs.error ? (
            <div className="section-body">
              <ErrorBlock message="We couldn't load SMS activity." onRetry={logs.reload} />
            </div>
          ) : !logs.data || logs.data.items.length === 0 ? (
            <Empty text="No SMS activity matches these filters." />
          ) : (
            <>
              <div className="table-meta">
                {formatNumber(logs.data.total)} message{logs.data.total === 1 ? "" : "s"}
              </div>
              <table className="table">
                <thead>
                  <tr>
                    <th>Time</th>
                    <th>Recipient</th>
                    <th>Type</th>
                    <th>Phone</th>
                    <th>Status</th>
                    <th className="right">Cost</th>
                  </tr>
                </thead>
                <tbody>
                  {logs.data.items.map((row) => {
                    const status = smsStatusPresentation(row.status);
                    return (
                      <tr key={row.id}>
                        <td className="small">{formatDateTime(row.sent_at)}</td>
                        <td className="small">{row.user_id}</td>
                        <td className="small">{smsKindLabel(row.kind)}</td>
                        <td className="small mono">{row.phone}</td>
                        <td>
                          <StatusPill {...status} />
                        </td>
                        <td className="small num right">
                          {row.cost != null && row.cost > 0 ? formatMoney(row.cost) : "—"}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              <Pager page={logs.data.page} pages={logs.data.pages} onChange={setPage} />
            </>
          )}
        </div>
      </section>
    </div>
  );
}