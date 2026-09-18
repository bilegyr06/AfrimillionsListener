// Single authoritative definition for the campaign statistics report, shared by
// the two renderers: the screen component (components/statistics/campaign-report)
// and the plain-text export (lib/snapshot). Row catalogs and field reads live
// here exactly once so the surfaces cannot drift.

import { formatDateTime } from "@/lib/format";
import type {
  Campaign,
  CampaignActivity,
  CampaignAudience,
  CampaignEconomics,
  CampaignFunnel,
  CampaignResponsePerformance,
  CampaignSmsPerformance,
  CampaignStatisticsDetail,
  CampaignWindow,
  GameStats,
  ResponseBucketKey,
} from "@/lib/types";

// Order of the time-to-first-play distribution rows mirrors the backend's fixed
// bucket contract (see ResponseBucketKey).
export const RESPONSE_BUCKET_ROWS: Array<[ResponseBucketKey, string]> = [
  ["lt_1h", "Under 1 hour"],
  ["1h_to_6h", "1 \u2013 6 hours"],
  ["6h_to_12h", "6 \u2013 12 hours"],
  ["12h_to_24h", "12 \u2013 24 hours"],
  ["ge_24h", "24 hours or more"],
];

// Order of the "not sent to" rows mirrors the backend's fixed bucket contract
// (see CampaignAudience.not_sent_to).
export const NOT_SENT_ROWS: Array<[keyof CampaignAudience["not_sent_to"], string]> = [
  ["disqualified_played", "Removed \u2014 played before send"],
  ["skipped_cap", "Skipped \u2014 limit reached"],
  ["skipped_cooldown", "Skipped \u2014 cooldown active"],
  ["skipped_invalid_phone", "Skipped \u2014 no valid phone"],
  ["failed_send", "SMS failed to send"],
  ["expired", "Expired with campaign"],
];

// View model over a CampaignStatisticsDetail: every field read the report
// surfaces, plus the two derived values (total not sent, attribution note).
// Renderers adapt this view, never the raw wire shape.
export interface CampaignReportData {
  campaign: Campaign;
  window: CampaignWindow;
  audience: CampaignAudience;
  funnel: CampaignFunnel;
  sms: CampaignSmsPerformance;
  response: CampaignResponsePerformance;
  activity: CampaignActivity;
  games: GameStats[];
  economics: CampaignEconomics;
  notSentTotal: number;
  attributionNote: string;
}

export function buildCampaignReport(detail: CampaignStatisticsDetail): CampaignReportData {
  const { campaign, window, audience, funnel, sms, response, activity, games, economics } =
    detail;
  const notSentTotal = Object.values(audience.not_sent_to).reduce((sum, n) => sum + n, 0);
  const attributionNote =
    campaign.status === "active"
      ? "SMS sent \u2192 now (rolling while active)"
      : `SMS sent \u2192 ${formatDateTime(window.attribution_end)}`;
  return {
    campaign,
    window,
    audience,
    funnel,
    sms,
    response,
    activity,
    games,
    economics,
    notSentTotal,
    attributionNote,
  };
}