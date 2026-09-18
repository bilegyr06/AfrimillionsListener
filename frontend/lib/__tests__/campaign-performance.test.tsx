import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import CampaignPerformance from "@/components/campaign-performance";
import type { CampaignStats } from "@/lib/types";

const stats: CampaignStats = {
  campaign_id: 7,
  opportunities: { created: 12, total: 2480, failed_send: 22 },
  interventions: { responded: 1184, total: 2093 },
  response_rate: 0.5657,
  avg_response_seconds: 83,
};

describe("CampaignPerformance", () => {
  it("renders the five metrics with formatted values", () => {
    const markup = renderToStaticMarkup(<CampaignPerformance stats={stats} />);
    for (const value of ["2,480", "2,093", "22", "1,184", "56.6%"]) {
      expect(markup).toContain(`>${value}<`);
    }
  });

  it("renders the metric labels in order", () => {
    const markup = renderToStaticMarkup(<CampaignPerformance stats={stats} />);
    const labels = ["Customers targeted", "SMS sent", "SMS failed", "Conversions", "Conversion rate"];
    let previous = -1;
    for (const label of labels) {
      const at = markup.indexOf(`>${label}<`);
      expect(at).toBeGreaterThan(previous);
      previous = at;
    }
  });

  it("accents the Conversions and Conversion rate values", () => {
    const markup = renderToStaticMarkup(<CampaignPerformance stats={stats} />);
    expect(markup).toContain('<div class="value accent">1,184</div>');
    expect(markup).toContain('<div class="value accent">56.6%</div>');
  });

  it("formats the average time to convert with formatDuration", () => {
    const markup = renderToStaticMarkup(<CampaignPerformance stats={stats} />);
    expect(markup).toContain(">Avg 1m 23s to convert<");
  });

  it("omits the average-time sub when the campaign has none", () => {
    const markup = renderToStaticMarkup(
      <CampaignPerformance stats={{ ...stats, avg_response_seconds: null }} />,
    );
    expect(markup).not.toContain("to convert");
  });

  it("reports still-pending customers when showPending is set", () => {
    const markup = renderToStaticMarkup(<CampaignPerformance stats={stats} showPending />);
    expect(markup).toContain("12 targeted customers still pending evaluation.");
  });

  it("confirms full evaluation when showPending is set and nothing is pending", () => {
    const markup = renderToStaticMarkup(
      <CampaignPerformance
        stats={{ ...stats, opportunities: { ...stats.opportunities, created: 0 } }}
        showPending
      />,
    );
    expect(markup).toContain("All targeted customers have been evaluated.");
  });

  it("renders no footnote unless showPending is set", () => {
    const markup = renderToStaticMarkup(<CampaignPerformance stats={stats} />);
    expect(markup).not.toContain("pending evaluation");
    expect(markup).not.toContain("have been evaluated");
  });
});