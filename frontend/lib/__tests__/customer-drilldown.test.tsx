// @vitest-environment jsdom

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CustomerDrilldown } from "@/components/statistics/customer-drilldown";
import type { CampaignCustomersResponse } from "@/lib/types";

const baseItems: CampaignCustomersResponse["items"] = [
  {
    user_id: "u1",
    first_name: "Ada",
    phone_raw: null,
    phone_normalized: "234-555",
    login_at: "2026-01-05T10:00:00.000Z",
    opportunity_status: "sent",
    intervention_status: "responded",
    sent_at: "2026-01-05T10:01:00.000Z",
    play_at: "2026-01-05T12:00:00.000Z",
    response_seconds: 7200,
    delivery_status: "sent",
    qualifying_plays: 3,
    attributed_amount: 2500,
    games_played: 2,
  },
  {
    user_id: "u2",
    first_name: null,
    phone_raw: "0801234567",
    phone_normalized: null,
    login_at: "2026-01-05T11:00:00.000Z",
    opportunity_status: "sent",
    intervention_status: "no_response",
    sent_at: "2026-01-05T11:01:00.000Z",
    play_at: null,
    response_seconds: null,
    delivery_status: "dnd",
    qualifying_plays: 0,
    attributed_amount: 0,
    games_played: 0,
  },
  {
    user_id: "u3",
    first_name: "Bo",
    phone_raw: null,
    phone_normalized: null,
    login_at: "2026-01-05T12:00:00.000Z",
    opportunity_status: "skipped_invalid_phone",
    intervention_status: null,
    sent_at: null,
    play_at: null,
    response_seconds: null,
    delivery_status: null,
    qualifying_plays: 0,
    attributed_amount: 0,
    games_played: 0,
  },
];

function campaignCustomers(items: CampaignCustomersResponse["items"], pages: number): CampaignCustomersResponse {
  return {
    campaign_id: 7,
    items,
    total: items.length,
    page: 1,
    page_size: 50,
    pages,
  };
}

function stubFetch(data: unknown): ReturnType<typeof vi.fn> {
  const mock = vi.fn(async () => ({
    ok: true,
    status: 200,
    json: async () => data,
  })) as unknown as ReturnType<typeof vi.fn>;
  vi.stubGlobal("fetch", mock);
  return mock;
}

async function renderDrilldown(campaignId: number) {
  const container = document.createElement("div");
  const root = createRoot(container);
  await act(async () => {
    root.render(<CustomerDrilldown campaignId={campaignId} />);
  });
  return { root, text: () => container.textContent ?? "" };
}

describe("CustomerDrilldown shared vocabulary consumption", () => {
  beforeEach(() => {
    (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders delivery labels and converted statuses from the shared vocabulary", async () => {
    const fetchMock = stubFetch(campaignCustomers(baseItems, 1));
    const view = await renderDrilldown(7);

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/campaign/7/customers?page=1&page_size=50",
      expect.objectContaining({ cache: "no-store" }),
    );
    const text = view.text();
    expect(text).toContain("Sent (awaiting delivery)");
    expect(text).toContain("Blocked (DND)");
    expect(text).toContain("Converted");
    expect(text).toContain("2h 0m");
  });

  it("keeps the audit table's raw statuses for non-converted outcomes", async () => {
    stubFetch(campaignCustomers(baseItems, 1));
    const view = await renderDrilldown(7);

    const text = view.text();
    expect(text).toContain("no_response");
    expect(text).toContain("skipped_invalid_phone");
    expect(text).toContain("—");
  });

  it("falls back to the raw status for an unknown delivery status", async () => {
    const items = baseItems.map((row) => ({ ...row, delivery_status: "stuck" }));
    stubFetch(campaignCustomers(items, 1));
    const view = await renderDrilldown(7);

    expect(view.text()).toContain("stuck");
  });

  it("lets Pager handle show/hide: no pager on a single page, pager on many", async () => {
    stubFetch(campaignCustomers(baseItems, 1));
    const single = await renderDrilldown(7);
    expect(single.text()).not.toContain("Page 1 of");

    vi.unstubAllGlobals();
    stubFetch(campaignCustomers(baseItems, 3));
    const multi = await renderDrilldown(7);
    expect(multi.text()).toContain("Page 1 of 3");
  });
});