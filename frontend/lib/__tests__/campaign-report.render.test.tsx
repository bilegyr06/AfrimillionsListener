import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import CampaignReport from "@/components/statistics/campaign-report";
import { campaignReportFixture } from "./fixtures";

// The dialog's `<pre>` embeds the plain-text snapshot, whose "Generated …"
// line uses the current wall clock. Normalise just that line so the markup is
// deterministic; everything else must match byte for byte.
function normalizeGeneratedLine(markup: string): string {
  return markup.replace(/Generated[^\n]*/, "Generated <at>");
}

describe("CampaignReport", () => {
  it("locks the rendered campaign report markup", () => {
    const markup = renderToStaticMarkup(
      <CampaignReport detail={campaignReportFixture} />,
    );
    expect(normalizeGeneratedLine(markup)).toMatchSnapshot();
  });
});