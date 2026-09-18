import { describe, expect, it } from "vitest";
import { buildStatisticsSnapshot } from "@/lib/snapshot";
import { campaignReportFixture } from "./fixtures";

describe("buildStatisticsSnapshot", () => {
  it("locks the plain-text campaign statistics report output", () => {
    const text = buildStatisticsSnapshot(
      campaignReportFixture,
      new Date("2026-01-05T10:30:00.000Z"),
    );
    expect(text).toMatchSnapshot();
  });
});