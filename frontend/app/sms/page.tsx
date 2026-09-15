"use client";

import { useState } from "react";
import Pager from "@/components/pager";
import StatusPill from "@/components/status-pill";
import { Empty, ErrorBlock, Loading } from "@/components/state-ui";
import { apiGet } from "@/lib/api";
import { formatDateTime, formatMoney, formatNumber, smsKindLabel, smsStatusPresentation } from "@/lib/format";
import type { Paged, SmsLogEntry } from "@/lib/types";
import { useQuery } from "@/lib/use-query";

const KIND_OPTIONS: { value: string; label: string }[] = [
  { value: "", label: "All types" },
  { value: "welcome", label: "Welcome" },
  { value: "inactive", label: "Inactive" },
  { value: "manual", label: "Manual" },
];

export default function SmsPage() {
  const [page, setPage] = useState(1);
  const [kind, setKind] = useState("");
  const [since, setSince] = useState("");
  const [until, setUntil] = useState("");

  const logs = useQuery<Paged<SmsLogEntry>>(
    () => apiGet("/sms/logs", { page, page_size: 50, kind, since, until }),
    [page, kind, since, until],
    15000,
  );

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
              {KIND_OPTIONS.map((opt) => (
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