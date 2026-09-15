"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import {
  CloseCampaignDialog,
  StartCampaignDialog,
  useCampaignActions,
} from "@/components/campaign-actions";
import Notice from "@/components/notice";
import StatusPill from "@/components/status-pill";
import { Empty, ErrorBlock, Loading } from "@/components/state-ui";
import { apiGet } from "@/lib/api";
import {
  campaignDisplayName,
  campaignStatusPresentation,
  formatDateTime,
  formatMoney,
  formatNumber,
  formatPercent,
  formatTime,
  smsKindLabel,
  smsStatusPresentation,
} from "@/lib/format";
import type { CampaignWithStats, Paged, ReportOverview, SmsLogEntry } from "@/lib/types";
import { useQuery } from "@/lib/use-query";

function Stat({ label, value, accent, sub }: { label: string; value: string; accent?: boolean; sub?: string }) {
  return (
    <div className="stat">
      <div className="label">{label}</div>
      <div className={`value${accent ? " accent" : ""}`}>{value}</div>
      {sub && <div className="sub">{sub}</div>}
    </div>
  );
}

export default function DashboardPage() {
  const router = useRouter();
  const [notice, setNotice] = useState<{ kind: "success" | "error"; text: string } | null>(null);
  const [startOpen, setStartOpen] = useState(false);
  const [closeOpen, setCloseOpen] = useState(false);
  const { busy, start, close } = useCampaignActions();

  const overview = useQuery<ReportOverview>(() => apiGet("/report/overview"), [], 30000);
  const activeCampaign = useQuery<{ active: boolean; campaign?: CampaignWithStats["campaign"] }>(
    () => apiGet("/campaign/current"),
    [],
  );
  const recent = useQuery<Paged<SmsLogEntry>>(() => apiGet("/sms/logs", { page_size: 8 }), []);

  const current = overview.data?.campaign.current ?? null;
  const active = overview.data?.campaign.active ?? false;

  const performance = useMemo(() => {
    if (!current) return null;
    const opps = current.opportunities;
    const ints = current.interventions;
    return {
      targeted: opps.total,
      sent: ints.total,
      failed: opps.failed_send ?? 0,
      conversions: ints.responded ?? 0,
      rate: current.response_rate,
      avgSeconds: current.avg_response_seconds,
      pending: opps.created ?? 0,
    };
  }, [current]);

  async function handleStart(name?: string) {
    try {
      const result = await start(name);
      setStartOpen(false);
      setNotice({ kind: "success", text: `Campaign "${campaignDisplayName(result.campaign.name, result.campaign.id)}" started.` });
      overview.reload();
      router.push(`/campaigns/${result.campaign.id}`);
    } catch (err) {
      setNotice({ kind: "error", text: err instanceof Error ? err.message : "Could not start the campaign." });
    }
  }

  async function handleClose() {
    try {
      const result = await close();
      setCloseOpen(false);
      setNotice({ kind: "success", text: `Campaign "${campaignDisplayName(result.campaign.name, result.campaign.id)}" closed and finalised.` });
      overview.reload();
    } catch (err) {
      setNotice({ kind: "error", text: err instanceof Error ? err.message : "Could not close the campaign." });
    }
  }

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
          <Loading text="Loading dashboard\u2026" />
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
        </div>
      </div>

      {notice && (
        <Notice kind={notice.kind} dismissMs={6000}>
          {notice.text}
        </Notice>
      )}

      {active && current ? (
        <section className="section">
          <div className="section-head">
            <div>
              <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                <h2>
                  {campaignDisplayName(activeCampaign.data?.campaign?.name ?? null, current.campaign_id)}
                </h2>
                <StatusPill {...campaignStatusPresentation("active")} />
              </div>
              <p className="muted" style={{ margin: "4px 0 0", fontSize: 13 }}>
                Started {formatDateTime(activeCampaign.data?.campaign?.started_at)}
              </p>
            </div>
            <div className="actions">
              <Link className="btn btn-secondary btn-sm" href={`/campaigns/${current.campaign_id}`}>
                View campaign
              </Link>
              <button className="btn btn-danger-secondary btn-sm" onClick={() => setCloseOpen(true)}>
                Close campaign
              </button>
            </div>
          </div>
          <div className="section-body flush" style={{ paddingBottom: 0 }}>
            {performance && (
              <div className="stat-strip" style={{ border: "none", borderRadius: 0 }}>
                <Stat label="Customers targeted" value={formatNumber(performance.targeted)} />
                <Stat label="SMS sent" value={formatNumber(performance.sent)} />
                <Stat label="SMS failed" value={formatNumber(performance.failed)} />
                <Stat label="Conversions" value={formatNumber(performance.conversions)} accent />
                <Stat
                  label="Conversion rate"
                  value={formatPercent(performance.rate)}
                  accent
                  sub={
                    performance.avgSeconds != null
                      ? `Avg ${performance.avgSeconds.toFixed(0)}s to convert`
                      : undefined
                  }
                />
              </div>
            )}
          </div>
          <div className="section-body" style={{ paddingTop: 12 }}>
            <p className="muted" style={{ margin: 0, fontSize: 13 }}>
              {performance && performance.pending > 0
                ? `${formatNumber(performance.pending)} targeted customers still pending evaluation.`
                : "All targeted customers have been evaluated."}
            </p>
          </div>
        </section>
      ) : (
        <section className="section">
          <div className="section-body">
            <Notice kind="info" dismissMs={0}>
              There is no active campaign right now.
            </Notice>
            <button className="btn btn-primary" onClick={() => setStartOpen(true)}>
              Start campaign
            </button>
          </div>
        </section>
      )}

      <section className="section">
        <div className="section-head">
          <h2>SMS traffic</h2>
        </div>
        <div className="section-body flush" style={{ paddingBottom: 0 }}>
          <div className="stat-strip" style={{ border: "none", borderRadius: 0 }}>
            <Stat label="Delivered" value={formatNumber(sms.delivered)} />
            <Stat label="Sent (awaiting delivery)" value={formatNumber(sms.sent - sms.delivered)} sub={`${formatNumber(sms.sent)} accepted total`} />
            <Stat label="Failed" value={formatNumber(sms.failed)} />
            <Stat label="Deferred" value={formatNumber(sms.deferred)} />
            <Stat label="Sent today" value={formatNumber(sms.today)} />
            <Stat label="Total cost" value={formatMoney(sms.total_cost)} />
          </div>
        </div>
        {data.wallet && (
          <div className="section-body" style={{ paddingTop: 12 }}>
            <p className="muted" style={{ margin: 0, fontSize: 13 }}>
              Balance {formatMoney(data.wallet.balance, data.wallet.currency)} · recorded {formatTime(data.wallet.fetched_at)}
            </p>
          </div>
        )}
      </section>

      <section className="section">
        <div className="section-head">
          <h2>Recent SMS activity</h2>
          <Link className="btn btn-secondary btn-sm" href="/sms">
            View all
          </Link>
        </div>
        <div className="section-body flush">
          {recent.loading && !recent.data ? (
            <Loading text="Loading SMS activity\u2026" />
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
      </section>

      <StartCampaignDialog open={startOpen} busy={busy} onConfirm={handleStart} onCancel={() => setStartOpen(false)} />
      <CloseCampaignDialog open={closeOpen} busy={busy} onConfirm={handleClose} onCancel={() => setCloseOpen(false)} />
    </div>
  );
}