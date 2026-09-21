"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import Notice from "@/components/notice";
import StatusPill from "@/components/status-pill";
import { Empty, ErrorBlock, Loading } from "@/components/state-ui";
import SnapshotDialog from "@/components/snapshot-dialog";
import WalletStatus from "@/components/wallet-status";
import { apiGet } from "@/lib/api";
import {
  assignmentMethodLabel,
  formatDate,
  formatDateTime,
  formatMoney,
  formatNumber,
  formatRelativeTime,
  reportStatePresentation,
  segmentLabel,
  smsKindLabel,
  smsStatusPresentation,
  windowDisplayName,
  windowStatusPresentation,
} from "@/lib/format";
import type {
  CampaignWindowRow,
  Paged,
  ReportOverview,
  SegmentsResponse,
  SmsLogEntry,
} from "@/lib/types";
import { useQuery } from "@/lib/use-query";
import { buildOverviewSnapshot, snapshotFilename } from "@/lib/snapshot";

function Stat({ label, value, accent, sub }: { label: string; value: string; accent?: boolean; sub?: string }) {
  return (
    <div className="stat">
      <div className="label">{label}</div>
      <div className={`value${accent ? " accent" : ""}`}>{value}</div>
      {sub && <div className="sub">{sub}</div>}
    </div>
  );
}

function ActiveWindowSection({
  windows,
  catalog,
  now,
  reload,
}: {
  windows: { data: CampaignWindowRow[] | null; loading: boolean; error: string | null };
  catalog?: SegmentsResponse["items"];
  now: Date;
  reload: () => void;
}) {
  const active = (windows.data ?? []).filter((w) => w.status === "active");
  const header = (
    <div className="section-head">
      <h2>Active campaign window</h2>
      <Link className="btn btn-secondary btn-sm" href="/windows">
        Windows
      </Link>
    </div>
  );

  if (windows.loading && !windows.data) {
    return (
      <div className="section">
        {header}
        <div className="section-body">
          <Loading text={"Loading campaign windows..."} />
        </div>
      </div>
    );
  }

  if (windows.error) {
    return (
      <div className="section">
        {header}
        <div className="section-body">
          <ErrorBlock message="We couldn't load campaign windows." onRetry={reload} />
        </div>
      </div>
    );
  }

  if (active.length === 0) {
    return (
      <div className="section">
        {header}
        <div className="section-body">
          <span className="muted" style={{ fontSize: 13 }}>
            No active campaign window right now.
          </span>
        </div>
      </div>
    );
  }

  return (
    <div className="section">
      {header}
      <div className="section-body flush">
        {active.map((w) => {
          const status = windowStatusPresentation(w.status);
          const reportState = reportStatePresentation(w.report_state);
          const split = w.split.actual;
          const splitText =
            split.total_users > 0
              ? `${formatNumber(split.campaign_users)} / ${formatNumber(split.control_users)}`
              : "No audience yet";
          return (
            <div key={w.id}>
              <div className="stat-strip" style={{ border: "none", borderRadius: 0 }}>
                <div className="stat">
                  <div className="label">Window</div>
                  <div className="value" style={{ fontSize: 16 }}>
                    <Link className="table-link" href={`/windows/${w.id}`}>
                      {windowDisplayName(w.name, w.id)}
                    </Link>
                  </div>
                  <div className="sub" style={{ display: "flex", gap: 6, marginTop: 4 }}>
                    <StatusPill {...status} />
                    <StatusPill {...reportState} />
                  </div>
                </div>
                <div className="stat">
                  <div className="label">Period</div>
                  <div className="value" style={{ fontSize: 16 }}>
                    {formatDate(w.start_time)}
                    {"→"}
                    {formatDate(w.end_time)}
                  </div>
                  <div className="sub">Finalization {formatRelativeTime(w.finalization_deadline, now)}</div>
                </div>
                <div className="stat">
                  <div className="label">Segments</div>
                  <div className="value" style={{ fontSize: 16 }}>
                    {w.selected_segments.map((id) => segmentLabel(id, catalog)).join(", ")}
                  </div>
                  <div className="sub">{`${assignmentMethodLabel(w.assignment_method)} assignment`}</div>
                </div>
                <div className="stat">
                  <div className="label">Eligible (N)</div>
                  <div className="value" style={{ fontSize: 16 }}>
                    {formatNumber(w.eligible_count)}
                  </div>
                  <div className="sub">
                    Campaign {formatNumber(split.campaign_users)}, Control {formatNumber(split.control_users)}
                  </div>
                </div>
                <div className="stat">
                  <div className="label">Campaign / Control</div>
                  <div className="value" style={{ fontSize: 16 }}>
                    {splitText}
                  </div>
                  <div className="sub">
                    {formatNumber(w.runs_count)} runs
                    {w.running_runs.length > 0 ? `${formatNumber(w.running_runs.length)} running` : ""}
                  </div>
                </div>
              </div>
              <div className="section-body">
                <Link className="btn btn-secondary btn-sm" href={`/windows/${w.id}`}>
                  Open window
                </Link>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default function DashboardPage() {
  const [notice, setNotice] = useState<{ kind: "success" | "error"; text: string } | null>(null);
  const [snapshotOpen, setSnapshotOpen] = useState(false);

  const overview = useQuery<ReportOverview>(() => apiGet("/report/overview"), [], 30000);
  const recent = useQuery<Paged<SmsLogEntry>>(() => apiGet("/sms/logs", { page_size: 8 }), []);
  const windows = useQuery<CampaignWindowRow[]>(() => apiGet("/windows"), [], 30000);
  const segments = useQuery<SegmentsResponse>(() => apiGet("/segments"), []);

  const [balanceAt, setBalanceAt] = useState<number | null>(null);
  const [nowTs, setNowTs] = useState(() => Date.now());
  const balance = useQuery<{ balance: number; currency: string }>(
    () =>
      apiGet<{ balance: number; currency: string }>("/stats/balance").then((b) => {
        setBalanceAt(Date.now());
        return b;
      }),
    [],
    15000,
  );
  useEffect(() => {
    const id = setInterval(() => setNowTs(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);
  useEffect(() => {
    const onVisible = () =>
      document.visibilityState === "visible" && balance.reload();
    window.addEventListener("focus", onVisible);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.removeEventListener("focus", onVisible);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [balance.reload]);

  if (overview.error) {
    return (
      <div className="page">
        <div className="page-head">
          <h1>Dashboard</h1>
          <div className="actions">
            <button className="btn btn-secondary" onClick={overview.reload}>
              Refresh
            </button>
          </div>
        </div>
        <div className="section">
          <div className="section-body">
            <ErrorBlock message="We couldn't load dashboard data." onRetry={overview.reload} />
          </div>
        </div>
      </div>
    );
  }

  if (overview.loading && !overview.data) {
    return (
      <div className="page">
        <h1>Dashboard</h1>
        <div className="section">
          <Loading text={"Loading dashboard..."} />
        </div>
      </div>
    );
  }

  const data = overview.data!;
  const sms = data.phone_sms;

  return (
    <div className="page">
      <div className="page-head">
        <h1>Dashboard</h1>
        <div className="actions">
          <button className="btn btn-secondary" onClick={overview.reload}>
            Refresh
          </button>
          <button className="btn btn-secondary" onClick={() => setSnapshotOpen(true)}>
            Export stats
          </button>
        </div>
      </div>

      {notice && (
        <Notice kind={notice.kind} dismissMs={6000}>
          {notice.text}
        </Notice>
      )}

      <ActiveWindowSection
        windows={windows}
        catalog={segments.data?.items}
        now={new Date(nowTs)}
        reload={windows.reload}
      />

      <div className="section">
        <div className="section-head">
          <h2>SMS traffic</h2>
        </div>
        <div className="section-body flush">
          <div className="stat-tier">
            <Stat label="Delivered" value={formatNumber(sms.delivered)} />
            <Stat label="Sent (awaiting delivery)" value={formatNumber(sms.sent - sms.delivered)} sub={`${formatNumber(sms.sent)} accepted total`} />
            <Stat label="Failed" value={formatNumber(sms.failed)} />
          </div>
          <div className="stat-tier-sm">
            <Stat label="Deferred" value={formatNumber(sms.deferred)} />
            <Stat label="Blocked (DND)" value={formatNumber(sms.dnd)} />
            <Stat label="Rejected" value={formatNumber(sms.rejected)} />
            <Stat label="Expired" value={formatNumber(sms.expired)} />
          </div>
          <div className="stat-tier">
            <Stat label="Sent today" value={formatNumber(sms.today)} />
            <Stat label="Total" value={formatNumber(sms.total)} />
            <Stat label="Total cost" value={formatMoney(sms.total_cost)} />
          </div>
          <WalletStatus
            balance={balance.data}
            balanceAt={balanceAt}
            nowTs={nowTs}
            error={balance.error}
            historical={data.wallet}
          />
        </div>
      </div>

      <div className="section">
        <div className="section-head">
          <h2>Recent SMS activity</h2>
          <Link className="btn btn-secondary btn-sm" href="/sms">
            View all
          </Link>
        </div>
        <div className="section-body flush">
          {recent.loading && !recent.data ? (
            <Loading text={"Loading SMS activity..."} />
          ) : recent.error ? (
            <div className="section-body">
              <ErrorBlock message="We couldn't load SMS activity." onRetry={recent.reload} />
            </div>
          ) : !recent.data || recent.data.items.length === 0 ? (
            <Empty text="No SMS activity yet." />
          ) : (
            <table className="table">
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Recipient</th>
                  <th>Type</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {recent.data.items.map((row) => {
                  const status = smsStatusPresentation(row.status);
                  return (
                    <tr key={row.id}>
                      <td className="small">{formatDateTime(row.sent_at)}</td>
                      <td className="small">{row.user_id}</td>
                      <td className="small">{smsKindLabel(row.kind)}</td>
                      <td>
                        <StatusPill {...status} />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      </div>

      <SnapshotDialog
        open={snapshotOpen}
        title="Statistics snapshot"
        text={data ? buildOverviewSnapshot(data) : ""}
        filename={snapshotFilename("afrimillions-stats")}
        onClose={() => setSnapshotOpen(false)}
      />
    </div>
  );
}