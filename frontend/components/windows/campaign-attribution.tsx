// Campaign SMS attribution for one report group. The Control group has no
// Campaign conversion model: those metrics render as N/A (never "0"), while the
// Campaign group shows the backend's attribution numbers verbatim. Execution
// (SMS delivery) stays out of this effectiveness surface.

import { Metric, MetricStrip, MetricTier } from "@/components/statistics/metric";
import StatSection from "@/components/statistics/section";
import { formatMoney, formatNumber, formatPercent, formatRatio } from "@/lib/format";
import type { WindowGroupMetrics } from "@/lib/types";

interface Props {
  group: WindowGroupMetrics;
}

export default function CampaignAttribution({ group }: Props) {
  if (group.conversion_not_applicable) {
    return (
      <StatSection title="Campaign SMS attribution">
        <div className="section-body">
          <p className="muted" style={{ margin: 0 }}>
            Control users never receive Campaign SMS and have no Campaign conversion model; these
            metrics are not applicable (N/A) to the Control group.
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