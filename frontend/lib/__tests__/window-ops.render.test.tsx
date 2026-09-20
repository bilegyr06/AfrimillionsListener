import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import CampaignAttribution from "@/components/windows/campaign-attribution";
import SnapshotCell from "@/components/windows/snapshot-cell";
import SplitSection from "@/components/windows/split-section";
import StatusBanner from "@/components/windows/status-banner";
import type { WindowGroupMetrics, WindowSplitSummary } from "@/lib/types";

const NOW = new Date("2026-09-16T09:00:00.000Z");

const splitFixture: WindowSplitSummary = {
  assignment_method: "deterministic",
  control_override: 15,
  recommended: { campaign_percentage: 80, control_percentage: 20 },
  effective: { campaign_percentage: 85, control_percentage: 15 },
  actual: {
    campaign_users: 1700,
    control_users: 300,
    total_users: 2000,
    campaign_percentage: 85,
    control_percentage: 15,
  },
};

// The Control group's attribution definition: no Campaign SMS reach it, so the
// conversion model does not apply and must render N/A, never "0".
const controlGroupFixture: WindowGroupMetrics = {
  assignment: "control",
  total_targeted_audience: 300,
  total_logged_in_users: 90,
  total_played_users: 60,
  total_deposited_users: 45,
  total_sales: 120000,
  total_plays: 240,
  login_rate: 0.3,
  play_rate: 0.2,
  deposit_rate: 0.15,
  arpu: 400,
  arppu: 2000,
  plays_per_player: 4,
  active_days: 3,
  active_days_per_player: 1.5,
  multi_day_players: 20,
  multi_day_player_rate: 0.333,
  deposit_to_play_rate: 0.8,
  login_to_play_rate: 0.6,
  deposited_no_play_users: 15,
  logged_in_no_play_users: 30,
  single_play_players: 25,
  multiple_play_players: 35,
  converted_users: null,
  contacted_users: null,
  conversion_rate: null,
  avg_plays_per_converted: null,
  avg_plays_per_contacted: null,
  avg_amount_per_converted: null,
  avg_amount_per_contacted: null,
  conversion_not_applicable: true,
};

const campaignGroupFixture: WindowGroupMetrics = {
  ...controlGroupFixture,
  assignment: "campaign",
  total_targeted_audience: 1700,
  total_played_users: 340,
  total_deposited_users: 255,
  total_sales: 680000,
  total_plays: 1360,
  login_rate: 0.4,
  play_rate: 0.2,
  deposit_rate: 0.15,
  arpu: 400,
  arppu: 2000,
  plays_per_player: 4,
  active_days: 4,
  active_days_per_player: 1.8,
  multi_day_players: 120,
  multi_day_player_rate: 0.353,
  deposited_no_play_users: 85,
  logged_in_no_play_users: 170,
  single_play_players: 140,
  multiple_play_players: 200,
  converted_users: 60,
  contacted_users: 1200,
  conversion_rate: 0.05,
  avg_plays_per_converted: 6,
  avg_plays_per_contacted: 1.1,
  avg_amount_per_converted: 8000,
  avg_amount_per_contacted: 560,
  conversion_not_applicable: false,
};

describe("window lifecycle banner", () => {
  it("renders the live window messaging with finalization deadline", () => {
    const markup = renderToStaticMarkup(
      <StatusBanner
        status="active"
        finalization_deadline="2026-09-20T14:00:00.000Z"
        finalized_at={null}
        now={NOW}
      />,
    );
    expect(markup).toContain("Window is live");
    expect(markup).toContain("4d left");
    expect(markup).toContain("20 Sept 2026, 14:00");
  });

  it("renders the grace-period messaging when the window ended", () => {
    const markup = renderToStaticMarkup(
      <StatusBanner
        status="ended"
        finalization_deadline="2026-09-20T14:00:00.000Z"
        finalized_at={null}
        now={NOW}
      />,
    );
    expect(markup).toContain("Window ended");
    expect(markup).toContain("Late data can still update the report");
    expect(markup).toContain("No new runs can");
  });

  it("renders the immutable finalized messaging", () => {
    const markup = renderToStaticMarkup(
      <StatusBanner
        status="finalized"
        finalization_deadline="2026-09-20T14:00:00.000Z"
        finalized_at="2026-09-20T13:00:00.000Z"
        now={NOW}
      />,
    );
    expect(markup).toContain("Finalized on");
    expect(markup).toContain("permanently frozen");
    expect(markup).toContain("late data no longer changes results");
  });
});

describe("window split presentation", () => {
  it("renders both sides of recommended, effective, and actual splits", () => {
    const markup = renderToStaticMarkup(<SplitSection split={splitFixture} />);
    expect(markup).toContain("Recommended split");
    expect(markup).toContain("Campaign 80% \u00b7 Control 20%");
    expect(markup).toContain("Effective split");
    expect(markup).toContain("Campaign 85% \u00b7 Control 15%");
    expect(markup).toContain("1,700 Campaign \u00b7 300 Control");
    expect(markup).toContain("overridden by the operator");
    expect(markup).toContain("never reassigned by a later run or upload");
  });
});

describe("campaign attribution", () => {
  it("renders N/A (not 0) for the Control group", () => {
    const markup = renderToStaticMarkup(<CampaignAttribution group={controlGroupFixture} />);
    expect(markup).toContain("not applicable (N/A)");
    expect(markup).not.toContain("0%");
  });

  it("renders the backend attribution metrics for the Campaign group", () => {
    const markup = renderToStaticMarkup(<CampaignAttribution group={campaignGroupFixture} />);
    expect(markup).toContain("1,200");
    expect(markup).toContain("60");
    expect(markup).toContain("5%");
    expect(markup).toContain("8,000.00");
  });
});

describe("run snapshot cell", () => {
  it("renders the frozen snapshot note and its files", () => {
    const markup = renderToStaticMarkup(
      <SnapshotCell
        capturedAt="2026-09-16T08:00:00.000Z"
        files={[
          { dataset: "Login", filename: "Login_20260916.csv", size_bytes: 100, mtime: "2026-09-16T07:00:00.000Z", row_count: 5000 },
          { dataset: "Sales", filename: "Sales_20260916.csv", size_bytes: 200, mtime: "2026-09-16T07:00:00.000Z", row_count: 800 },
        ]}
      />,
    );
    expect(markup).toContain("2 files");
    expect(markup).toContain("16 Sept 2026, 08:00");
    expect(markup).toContain("Logins");
    expect(markup).toContain("Later uploads do not change this run");
    expect(markup).toContain("5,000");
  });

  it("renders 'No snapshot' when a run has none", () => {
    const markup = renderToStaticMarkup(<SnapshotCell capturedAt={null} files={[]} />);
    expect(markup).toContain("No snapshot");
  });
});