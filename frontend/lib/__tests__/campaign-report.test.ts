import { describe, expect, it } from "vitest";
import { formatDateTime } from "@/lib/format";
import {
  buildCampaignReport,
  NOT_SENT_ROWS,
  RESPONSE_BUCKET_ROWS,
} from "@/lib/campaign-report";
import { campaignReportFixture } from "./fixtures";

function asActive(detail: typeof campaignReportFixture) {
  const copy = structuredClone(detail);
  copy.campaign.status = "active";
  return copy;
}

describe("RESPONSE_BUCKET_ROWS", () => {
  it("mirrors the backend's fixed bucket keys in order", () => {
    expect(RESPONSE_BUCKET_ROWS.map(([key]) => key)).toEqual([
      "lt_1h",
      "1h_to_6h",
      "6h_to_12h",
      "12h_to_24h",
      "ge_24h",
    ]);
  });

  it("carries the display labels used by both renderers", () => {
    expect(RESPONSE_BUCKET_ROWS.map(([, label]) => label)).toEqual([
      "Under 1 hour",
      "1 \u2013 6 hours",
      "6 \u2013 12 hours",
      "12 \u2013 24 hours",
      "24 hours or more",
    ]);
  });
});

describe("NOT_SENT_ROWS", () => {
  it("mirrors the backend's not-sent keys in order", () => {
    expect(NOT_SENT_ROWS.map(([key]) => key)).toEqual([
      "disqualified_played",
      "skipped_cap",
      "skipped_cooldown",
      "skipped_invalid_phone",
      "failed_send",
      "expired",
    ]);
  });

  it("uses the unified display labels in both renderers", () => {
    const disqualified = NOT_SENT_ROWS[0];
    expect(disqualified).toEqual([
      "disqualified_played",
      "Removed \u2014 played before send",
    ]);
  });
});

describe("buildCampaignReport", () => {
  it("exposes every report section plus the derived values", () => {
    const r = buildCampaignReport(campaignReportFixture);
    expect(r.campaign).toBe(campaignReportFixture.campaign);
    expect(r.window).toBe(campaignReportFixture.window);
    expect(r.audience).toBe(campaignReportFixture.audience);
    expect(r.funnel).toBe(campaignReportFixture.funnel);
    expect(r.sms).toBe(campaignReportFixture.sms);
    expect(r.response).toBe(campaignReportFixture.response);
    expect(r.activity).toBe(campaignReportFixture.activity);
    expect(r.games).toBe(campaignReportFixture.games);
    expect(r.economics).toBe(campaignReportFixture.economics);
  });

  it("sums the not-sent buckets into notSentTotal", () => {
    const r = buildCampaignReport(campaignReportFixture);
    const parts = Object.values(campaignReportFixture.audience.not_sent_to);
    expect(r.notSentTotal).toBe(parts.reduce((sum, n) => sum + n, 0));
  });

  it("builds the attribution note for a closed campaign from its window end", () => {
    const r = buildCampaignReport(campaignReportFixture);
    expect(r.attributionNote).toBe(
      `SMS sent \u2192 ${formatDateTime(campaignReportFixture.window.attribution_end)}`,
    );
  });

  it("builds the rolling attribution note for an active campaign", () => {
    const r = buildCampaignReport(asActive(campaignReportFixture));
    expect(r.attributionNote).toBe("SMS sent \u2192 now (rolling while active)");
  });
});