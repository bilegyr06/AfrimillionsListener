// Campaign / Control split presentation for a window. Every number is the
// backend's split summary (recommended formula, effective after any operator
// override, and the actual assigned audience); the UI only renders both sides
// of each split and never recomputes a percentage.

import { Metric, MetricStrip } from "@/components/statistics/metric";
import StatSection from "@/components/statistics/section";
import { formatNumber, formatPercentage } from "@/lib/format";
import type { WindowSplitSummary } from "@/lib/types";

interface Props {
  split: WindowSplitSummary;
}

function SplitMetric({
  title,
  side,
}: {
  title: string;
  side: { campaign_percentage: number; control_percentage: number } | null;
}) {
  if (!side) return null;
  return (
    <Metric
      label={title}
      value={`Campaign ${formatPercentage(side.campaign_percentage)}, Control ${formatPercentage(side.control_percentage)}`}
    />
  );
}

export default function SplitSection({ split }: Props) {
  return (
    <StatSection title="Campaign / Control split">
      <div className="section-body flush">
        <MetricStrip>
          <SplitMetric title="Recommended split" side={split.recommended} />
          <SplitMetric title="Effective split" side={split.effective} />
          <Metric
            label="Actual (assigned)"
            value={
              split.actual.total_users > 0
                ? `${formatNumber(split.actual.campaign_users)} Campaign, ${formatNumber(split.actual.control_users)} Control`
                : "No audience yet"
            }
          />
        </MetricStrip>
      </div>
      <div className="section-body">
        {split.control_override !== null && split.control_override !== undefined && (
          <p className="hint" style={{ margin: "0 0 6px" }}>
            The recommended split was overridden by the operator (Control {formatPercentage(split.control_override)}).
          </p>
        )}
        <p className="hint" style={{ margin: "0 0 6px" }}>
          The effective Control percentage is established once and is fixed for the entire window:
          growing N never changes the split.
        </p>
        <p className="hint" style={{ margin: 0 }}>
          Assignment is per user for the whole window: once assigned to Campaign or Control, a user is
          never reassigned by a later run or upload.
        </p>
      </div>
    </StatSection>
  );
}