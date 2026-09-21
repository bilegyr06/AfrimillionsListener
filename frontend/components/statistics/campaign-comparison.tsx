// Descriptive multi-campaign comparison on the Statistics surface.
//
// The table is deliberately presentation-neutral: it lists comparable facts
// about each selected campaign (contacted, conversion rate, response time,
// qualifying plays, attributed play amount, SMS cost, cost per conversion,
// activity/cost ratio) and never ranks a "best" campaign. Interpretation is
// the operator's job. All rows are the same summary contract served by
// /stats/campaigns, so the numbers always match the overview.

"use client";

import { useEffect, useState } from "react";
import StatSection from "@/components/statistics/section";
import StatusPill from "@/components/status-pill";
import { Empty, ErrorBlock, Loading } from "@/components/state-ui";
import { apiPost } from "@/lib/api";
import {
  campaignDisplayName,
  campaignStatusPresentation,
  formatDate,
  formatDuration,
  formatMoney,
  formatNumber,
  formatPercent,
  formatRatio,
} from "@/lib/format";
import type { CampaignStatisticsSummary } from "@/lib/types";

const METRIC_COLUMNS: Array<[string, (s: CampaignStatisticsSummary) => string]> = [
  ["Customers targeted", (s) => formatNumber(s.audience.unique_customers)],
  ["Accepted (sent)", (s) => formatNumber(s.sms.accepted)],
  ["Customers contacted", (s) => formatNumber(s.sms.contacted_customers)],
  ["Delivered", (s) => formatNumber(s.sms.delivered)],
  ["Converted customers", (s) => formatNumber(s.response.converted_customers)],
  ["Conversion rate", (s) => formatPercent(s.response.conversion_rate)],
  [
    "Avg time to first play",
    (s) => (s.response.avg_response_seconds != null ? formatDuration(s.response.avg_response_seconds) : "\u2014"),
  ],
  ["Qualifying plays", (s) => formatNumber(s.activity.qualifying_plays)],
  ["Players", (s) => formatNumber(s.activity.players)],
  ["Attributed play amount", (s) => formatMoney(s.activity.total_play_amount)],
  ["SMS cost", (s) => formatMoney(s.economics.sms_cost)],
  ["Cost per conversion", (s) => formatMoney(s.economics.cost_per_conversion)],
  ["Activity / cost ratio", (s) => formatRatio(s.economics.activity_cost_ratio)],
];

interface Props {
  ids: number[];
  onClose: () => void;
}

export function CampaignComparison({ ids, onClose }: Props) {
  const [data, setData] = useState<CampaignStatisticsSummary[] | null>(null);
  const [error, setError] = useState(false);
  const [loading, setLoading] = useState(true);
  const [retry, setRetry] = useState(0);

  const key = `${ids.join(",")}::${retry}`;

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(false);
    setData(null);
    apiPost<CampaignStatisticsSummary[]>("/stats/campaigns/compare", {
      campaign_ids: ids,
    })
      .then((rows) => {
        if (!cancelled) setData(rows);
      })
      .catch(() => {
        if (!cancelled) setError(true);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  return (
    <StatSection
      title={`Compare ${data?.length ?? ids.length} campaigns`}
      aside={
        <div className="actions" style={{ margin: 0 }}>
          <button className="btn btn-secondary btn-sm" onClick={onClose}>
            Close comparison
          </button>
        </div>
      }
    >
      {loading && !data ? (
        <div className="section-body">
          <Loading text={"Loading comparison..."} />
        </div>
      ) : error ? (
        <div className="section-body">
          <ErrorBlock message="We couldn't load the campaign comparison." onRetry={() => setRetry((n) => n + 1)} />
        </div>
      ) : !data || data.length === 0 ? (
        <div className="section-body">
          <Empty text="No campaigns to compare." />
        </div>
      ) : (
        <div className="section-body" style={{ overflowX: "auto" }}>
          <table className="table">
            <thead>
              <tr>
                <th>Metric</th>
                {data.map((c) => (
                  <th key={c.campaign_id} className="num right">
                    {campaignDisplayName(c.name, c.campaign_id)}
                    <StatusPill {...campaignStatusPresentation(c.status)} />
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {METRIC_COLUMNS.map(([label, cell]) => (
                <tr key={label}>
                  <td className="small">{label}</td>
                  {data.map((c) => (
                    <td key={c.campaign_id} className="small num right">
                      {cell(c)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
          <p className="muted" style={{ margin: "10px 20px 0", fontSize: 12.5 }}>
            Comparison is descriptive only \u2014 there is no automatic ranking.
            Attributed play amount is qualifying plays inside each campaign\u2019s
            attribution window; the activity/cost ratio is not ROI.
          </p>
        </div>
      )}
    </StatSection>
  );
}