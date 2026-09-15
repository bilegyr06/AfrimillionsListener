// Campaign selector for the Statistics surface. The population is the same
// /stats/campaigns summary list already served to the overview, so switching
// campaign of interest never triggers extra requests on its own.

"use client";

import { campaignDisplayName, formatDate } from "@/lib/format";
import type { CampaignStatisticsSummary } from "@/lib/types";

interface Props {
  campaigns: CampaignStatisticsSummary[];
  value: number | null;
  onChange: (campaignId: number) => void;
  disabled?: boolean;
  /** Label used when `value` is not present in `campaigns` (e.g. deep link to
   *  a campaign outside the summaries window). */
  fallbackLabel?: string;
}

export function CampaignSelect({
  campaigns,
  value,
  onChange,
  disabled = false,
  fallbackLabel,
}: Props) {
  const hasData = campaigns.length > 0;
  const missing = value != null && !campaigns.some((c) => c.campaign_id === value);
  return (
    <label className="filter-bar" style={{ gap: 8 }}>
      <span className="muted small">Campaign</span>
      <select
        value={value ?? ""}
        disabled={disabled || !hasData}
        onChange={(e) => {
          const next = Number(e.target.value);
          if (Number.isFinite(next)) onChange(next);
        }}
      >
        {!hasData && <option value="">No campaigns yet</option>}
        {missing && (
          <option value={value ?? ""}>{fallbackLabel ?? "This campaign"}</option>
        )}
        {campaigns.map((c) => (
          <option key={c.campaign_id} value={c.campaign_id}>
            {campaignDisplayName(c.name, c.campaign_id)}
            {c.status === "active" ? " \u2014 Active" : ""}
            {" \u00b7 "}
            {formatDate(c.started_at)}
          </option>
        ))}
      </select>
    </label>
  );
}