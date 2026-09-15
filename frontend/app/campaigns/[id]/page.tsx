"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { CloseCampaignDialog, useCampaignActions } from "@/components/campaign-actions";
import Notice from "@/components/notice";
import Pager from "@/components/pager";
import StatusPill from "@/components/status-pill";
import { Empty, ErrorBlock, Loading } from "@/components/state-ui";
import { apiGet } from "@/lib/api";
import {
  campaignDisplayName,
  campaignStatusPresentation,
  customerOutcomePresentation,
  formatDate,
  formatDateTime,
  formatDuration,
  formatNumber,
  formatPercent,
  formatTime,
} from "@/lib/format";
import type {
  Campaign,
  CampaignCustomersResponse,
  CampaignStats,
  SettingRow,
  SettingsResponse,
} from "@/lib/types";
import { useQuery } from "@/lib/use-query";

function humanizeKey(key: string): string {
  return key
    .split("_")
    .map((w) => w.charAt(0) + w.slice(1).toLowerCase())
    .join(" ");
}

function renderConfigValue(meta: SettingRow | undefined, raw: string): { value: string; mono?: boolean } {
  if (meta?.input_type === "checkbox") return { value: raw === "true" ? "Enabled" : "Disabled" };
  if (meta?.input_type === "tags") return { value: raw.split(",").filter(Boolean).join(", ") };
  if (meta?.input_type === "time") return { value: raw };
  if (meta?.kind === "set") return { value: raw.split(",").filter(Boolean).join(", ") };
  if (meta?.input_type === "textarea") return { value: raw, mono: true };
  return { value: raw };
}

export default function CampaignDetailPage() {
  const params = useParams<{ id: string }>();
  const campaignId = Number(params.id);

  const [page, setPage] = useState(1);
  const [closeOpen, setCloseOpen] = useState(false);
  const [notice, setNotice] = useState<{ kind: "success" | "error"; text: string } | null>(null);
  const { busy, close } = useCampaignActions();

  const detail = useQuery<{ campaign: Campaign; stats: CampaignStats }>(
    () => apiGet(`/campaign/${campaignId}`),
    [campaignId],
  );
  const customers = useQuery<CampaignCustomersResponse>(
    () =>
      apiGet(`/campaign/${campaignId}/customers`, {
        page,
        page_size: 50,
      }),
    [campaignId, page],
  );
  const settingsMeta = useQuery<SettingsResponse>(() => apiGet("/settings"), [], 60000);
  const specByKey = new Map(settingsMeta.data?.items.map((s) => [s.key, s]) ?? []);

  async function handleClose() {
    try {
      const result = await close();
      setCloseOpen(false);
      setNotice({
        kind: "success",
        text: `Campaign "${campaignDisplayName(result.campaign.name, result.campaign.id)}" closed and finalised.`,
      });
      detail.reload();
    } catch (err) {
      setNotice({ kind: "error", text: err instanceof Error ? err.message : "Could not close the campaign." });
    }
  }

  if (detail.loading && !detail.data) {
    return (
      <div className="page">
        <h1>Campaign</h1>
        <div className="section">
          <Loading text="Loading campaign\u2026" />
        </div>
      </div>
    );
  }

  if (detail.error || !detail.data) {
    return (
      <div className="page">
        <div className="page-head">
          <h1>Campaign</h1>
          <Link className="btn btn-secondary" href="/campaigns">
            Back to campaigns
          </Link>
        </div>
        <div className="section">
          <div className="section-body">
            <ErrorBlock message="We couldn't load this campaign." onRetry={detail.reload} />
          </div>
        </div>
      </div>
    );
  }

  const { campaign, stats } = detail.data;
  const status = campaignStatusPresentation(campaign.status);
  const isActive = campaign.status === "active";
  const configEntries = Object.entries(campaign.config ?? {}).filter(
    ([key, value]) => specByKey.has(key) && value !== "",
  );

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <h1>{campaignDisplayName(campaign.name, campaign.id)}</h1>
            <StatusPill {...status} />
          </div>
          {isActive && (
            <p className="muted" style={{ margin: "4px 0 0", fontSize: 13 }}>
              Running since {formatDateTime(campaign.started_at)}
            </p>
          )}
        </div>
        <div className="actions">
          <Link className="btn btn-secondary" href="/campaigns">
            Back to campaigns
          </Link>
          {isActive && (
            <button className="btn btn-danger-secondary" onClick={() => setCloseOpen(true)}>
              Close campaign
            </button>
          )}
        </div>
      </div>

      {notice && (
        <Notice kind={notice.kind} dismissMs={6000}>
          {notice.text}
        </Notice>
      )}

      <section className="section">
        <div className="section-head">
          <h2>Campaign overview</h2>
        </div>
        <div className="section-body flush" style={{ paddingBottom: 0 }}>
          <div className="stat-strip" style={{ border: "none", borderRadius: 0 }}>
            <div className="stat">
              <div className="label">Customers targeted</div>
              <div className="value">{formatNumber(stats.opportunities.total)}</div>
            </div>
            <div className="stat">
              <div className="label">SMS sent</div>
              <div className="value">{formatNumber(stats.interventions.total)}</div>
            </div>
            <div className="stat">
              <div className="label">SMS failed</div>
              <div className="value">{formatNumber(stats.opportunities.failed_send ?? 0)}</div>
            </div>
            <div className="stat">
              <div className="label">Conversions</div>
              <div className="value accent">{formatNumber(stats.interventions.responded ?? 0)}</div>
            </div>
            <div className="stat">
              <div className="label">Conversion rate</div>
              <div className="value accent">{formatPercent(stats.response_rate)}</div>
              {stats.avg_response_seconds != null && (
                <div className="sub">Avg {formatDuration(stats.avg_response_seconds)} to convert</div>
              )}
            </div>
          </div>
        </div>
        <div className="section-body" style={{ paddingTop: 0 }}>
          <table className="kv">
            <tbody>
              <tr>
                <th>Period</th>
                <td>
                  {formatDate(campaign.started_at)}
                  {"\u2009\u2192\u2009"}
                  {campaign.ended_at ? formatDate(campaign.ended_at) : "present"}
                </td>
              </tr>
              <tr>
                <th>Still awaiting evaluation</th>
                <td>{formatNumber(stats.opportunities.created ?? 0)} customers</td>
              </tr>
              <tr>
                <th>Not sent — invalid phone</th>
                <td>{formatNumber(stats.opportunities.skipped_invalid_phone ?? 0)} customers</td>
              </tr>
              <tr>
                <th>Removed — played before send</th>
                <td>{formatNumber(stats.opportunities.disqualified_played ?? 0)} customers</td>
              </tr>
              <tr>
                <th>Skipped — limit or cooldown</th>
                <td>
                  {formatNumber((stats.opportunities.skipped_cap ?? 0) + (stats.opportunities.skipped_cooldown ?? 0))} customers
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      {configEntries.length > 0 && (
        <section className="section">
          <div className="section-head">
            <h2>Configuration used</h2>
            <span className="muted" style={{ fontSize: 12.5 }}>
              Snapshot taken when the campaign started
            </span>
          </div>
          <div className="section-body flush">
            <table className="kv">
              <tbody>
                {configEntries.map(([key, raw]) => {
                  const meta = specByKey.get(key);
                  const rendered = renderConfigValue(meta, raw);
                  return (
                    <tr key={key}>
                      <th>
                        {meta?.label ?? humanizeKey(key)}
                        {meta?.description && (
                          <div className="muted" style={{ fontSize: 12.5, marginTop: 2 }}>
                            {meta.description}
                          </div>
                        )}
                      </th>
                      <td className={rendered.mono ? "mono" : undefined}>{rendered.value}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>
      )}

      <section className="section">
        <div className="section-head">
          <h2>Customers</h2>
          <span className="muted" style={{ fontSize: 12.5 }}>
            {customers.data ? formatNumber(customers.data.total) : "\u00a0"} targeted
          </span>
        </div>
        <div className="section-body flush">
          {customers.loading && !customers.data ? (
            <Loading text="Loading customers\u2026" />
          ) : customers.error ? (
            <div className="section-body">
              <ErrorBlock message="We couldn't load this campaign's customers." onRetry={customers.reload} />
            </div>
          ) : !customers.data || customers.data.items.length === 0 ? (
            <Empty text="No customers have been targeted in this campaign." />
          ) : (
            <>
              <table className="table">
                <thead>
                  <tr>
                    <th>Customer</th>
                    <th>Targeted</th>
                    <th>Outcome</th>
                    <th>SMS time</th>
                    <th>Converted</th>
                  </tr>
                </thead>
                <tbody>
                  {customers.data.items.map((row) => {
                    const outcome = customerOutcomePresentation(row);
                    return (
                      <tr key={row.user_id + row.login_at}>
                        <td>
                          <div>{row.first_name || row.user_id}</div>
                          <div className="muted small">{row.user_id}</div>
                        </td>
                        <td className="small">
                          {formatDate(row.login_at)}
                          <div className="muted small">{formatTime(row.login_at)}</div>
                        </td>
                        <td>
                          <StatusPill {...outcome} />
                        </td>
                        <td className="small">{formatDateTime(row.sent_at)}</td>
                        <td className="small">
                          {row.play_at ? (
                            <>
                              {formatDateTime(row.play_at)}
                              {row.response_seconds != null && (
                                <div className="muted small">
                                  {formatDuration(row.response_seconds)} after SMS
                                </div>
                              )}
                            </>
                          ) : (
                            "\u2014"
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              {customers.data && (
                <Pager page={customers.data.page} pages={customers.data.pages} onChange={setPage} />
              )}
            </>
          )}
        </div>
      </section>

      <CloseCampaignDialog open={closeOpen} busy={busy} onConfirm={handleClose} onCancel={() => setCloseOpen(false)} />
    </div>
  );
}