import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import WalletStatus from "@/components/wallet-status";

// The wallet surface must keep live-fresh / error-with-historical / loading
// states visually distinct: a failed live request is labelled as such and never
// posed as the current balance.
describe("WalletStatus", () => {
  it("renders the fresh live balance with its age", () => {
    const markup = renderToStaticMarkup(
      <WalletStatus
        balance={{ balance: 320000, currency: "NGN" }}
        balanceAt={1726495200000}
        nowTs={1726495260000}
        error={null}
        historical={null}
      />,
    );
    expect(markup).toContain("Live balance");
    expect(markup).toContain("NGN 320,000.00");
    expect(markup).toContain("Updated 60s ago");
  });

  it("shows the historical value with an explicit live-balance error", () => {
    const markup = renderToStaticMarkup(
      <WalletStatus
        balance={null}
        balanceAt={1726495200000}
        nowTs={1726495260000}
        error="Network error"
        historical={{ id: 1, balance: 315000, currency: "NGN", fetched_at: "2026-09-16T09:00:00.000Z" }}
      />,
    );
    expect(markup).toContain("Live balance unavailable (Network error)");
    expect(markup).toContain("recorded 09:00");
  });

  it("renders loading while no balance and no error yet", () => {
    const markup = renderToStaticMarkup(
      <WalletStatus
        balance={{ balance: 0, currency: "NGN" }}
        balanceAt={null}
        nowTs={1726495260000}
        error={null}
        historical={null}
      />,
    );
    expect(markup).toContain("Live balance");
    expect(markup).toContain("checking…");
    expect(markup).not.toContain("recorded");
    expect(markup).not.toContain("Unavailable");
  });
});