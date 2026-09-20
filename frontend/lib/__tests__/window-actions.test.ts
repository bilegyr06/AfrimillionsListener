import { describe, expect, it } from "vitest";
import { dispatchResultText, evaluateResultText } from "@/lib/window-actions";

describe("evaluateResultText", () => {
  it("summarises the evaluation outcome into readable operator copy", () => {
    const text = evaluateResultText({
      run_id: 7,
      window_id: 3,
      window_status: "active",
      evaluated_at: "2026-09-16T09:00:00.000Z",
      candidates: 2500,
      decisions: { eligible: 2100, cooldown: 300, invalid_phone: 100 },
      members_by_segment: { unsegmented: 2100 },
      audience: { added: 2000, existing: 100, campaign: 1600, control: 400, invalid_phone: 100 },
      eligible_count: 2100,
    });
    expect(text).toContain("Evaluated 2,500 candidates");
    expect(text).toContain("Added 2,000 (1,600 Campaign / 400 Control)");
    expect(text).toContain("100 already present");
    expect(text).toContain("100 invalid phones");
    expect(text).toContain("Eligible count is now 2,100");
  });
});

describe("dispatchResultText", () => {
  it("separates SMS execution counts (sent/failed/deferred + skips) from automation", () => {
    const text = dispatchResultText({
      run_id: 7,
      window_id: 3,
      run_status: "running",
      dispatched_at: "2026-09-16T09:05:00.000Z",
      target: 1600,
      already_sent: 300,
      skipped_cooldown: 80,
      skipped_cap: 20,
      sent: 1150,
      failed: 40,
      deferred: 10,
    });
    expect(text).toContain("targeted 1,600");
    expect(text).toContain("Sent 1,150, failed 40, deferred 10");
    expect(text).toContain("300 already sent");
    expect(text).toContain("80 skipped (cooldown)");
    expect(text).toContain("20 skipped (limit)");
  });
});