"use client";

// Shared five-metric performance strip for a campaign's live numbers. Used by
// the dashboard's active-campaign section and the campaign detail page's
// overview. Purely presentational: it takes the stats directly and does not
// fetch, poll, or handle focus refresh.

import { Metric } from "@/components/statistics/metric";
import { formatDuration, formatNumber, formatPercent } from "@/lib/format";
import type { CampaignStats } from "@/lib/types";

interface CampaignPerformanceProps {
  stats: CampaignStats;
  showPending?: boolean;
}

export default function CampaignPerformance({ stats, showPending = false }: CampaignPerformanceProps) {
  const pending = stats.opportunities.created ?? 0;
  return (
    <>
      <div className="section-body flush" style={{ paddingBottom: 0 }}>
        <div className="stat-strip flush">
          <Metric label="Customers targeted" value={formatNumber(stats.opportunities.total)} />
          <Metric label="SMS sent" value={formatNumber(stats.interventions.total)} />
          <Metric label="SMS failed" value={formatNumber(stats.opportunities.failed_send ?? 0)} />
          <Metric label="Conversions" value={formatNumber(stats.interventions.responded ?? 0)} accent />
          <Metric
            label="Conversion rate"
            value={formatPercent(stats.response_rate)}
            accent
            sub={
              stats.avg_response_seconds != null
                ? `Avg ${formatDuration(stats.avg_response_seconds)} to convert`
                : undefined
            }
          />
        </div>
      </div>
      {showPending && (
        <div className="section-body" style={{ paddingTop: 12 }}>
          <p className="hint" style={{ margin: 0 }}>
            {pending > 0
              ? `${formatNumber(pending)} targeted customers still pending evaluation.`
              : "All targeted customers have been evaluated."}
          </p>
        </div>
      )}
    </>
  );
}