import { describe, expect, it } from "vitest";
import {
  customerOutcomePresentation,
  deliveryStatusLabel,
  formatDate,
  formatDateTime,
  formatMoney,
  formatNumber,
  formatPercent,
  formatRatio,
  formatTime,
  isCustomerConverted,
  smsStatusPresentation,
} from "@/lib/format";

// Freezes the intended, locale-specific output of the formatters the campaign
// report uses. lib/format.ts pins its locale explicitly, so these strings must
// hold on every machine regardless of the host OS locale; if that pinning is
// ever loosened, this test fails wherever the host default differs.
describe("format (report locale)", () => {
  it("formats dates British-day style with a 24-hour clock", () => {
    expect(formatDate("2026-01-05T10:00:00.000Z")).toBe("5 Jan 2026");
    expect(formatTime("2026-01-05T10:30:00.000Z")).toBe("10:30");
    expect(formatDateTime("2026-01-05T10:30:00.000Z")).toBe("5 Jan 2026, 10:30");
    expect(formatDateTime("2026-01-05T11:12:00.000Z")).toBe("5 Jan 2026, 11:12");
  });

  it("formats numbers, money, percent, and ratio with the pinned separators", () => {
    expect(formatNumber(2480)).toBe("2,480");
    expect(formatMoney(4186)).toBe("4,186.00");
    expect(formatMoney(12500.5)).toBe("12,500.50");
    expect(formatPercent(0.5657)).toBe("56.6%");
    expect(formatRatio(1.78)).toBe("1.78");
  });
});

// The customer/SMS delivery vocabulary is a decision table owned by
// lib/format.ts. These tests freeze every label so the drill-down table and the
// SMS surfaces cannot drift apart again.
describe("deliveryStatusLabel (shared delivery vocabulary)", () => {
  const labels: Array<[string, string]> = [
    ["delivered", "Delivered"],
    ["sent", "Sent (awaiting delivery)"],
    ["failed", "Failed"],
    ["rejected", "Rejected"],
    ["expired", "Expired"],
    ["deferred", "Deferred"],
    ["dnd", "Blocked (DND)"],
  ];

  it.each(labels)("maps %s to its label", (status, label) => {
    expect(deliveryStatusLabel(status)).toBe(label);
  });

  it("is the same vocabulary the SMS surfaces present", () => {
    for (const [status] of labels) {
      expect(deliveryStatusLabel(status)).toBe(smsStatusPresentation(status).label);
    }
  });

  it("documents the canonical 'sent' wording", () => {
    expect(deliveryStatusLabel("sent")).toBe("Sent (awaiting delivery)");
  });

  it("falls back to the raw status for unknown values", () => {
    expect(deliveryStatusLabel("stuck")).toBe("stuck");
  });

  it("renders an em dash when there is no delivery status", () => {
    expect(deliveryStatusLabel(null)).toBe("—");
    expect(deliveryStatusLabel(undefined)).toBe("—");
  });
});

describe("customerOutcomePresentation (outcome decision table)", () => {
  it.each([
    { opp: "sent", int: "responded", label: "Converted", tone: "ok" },
    { opp: "open", int: "responded", label: "Converted", tone: "ok" },
    { opp: "sent", int: "no_response", label: "Sent, no conversion", tone: "neutral" },
    { opp: "created", int: "open", label: "Sent — awaiting response", tone: "accent" },
    { opp: "sent", int: null, label: "Sent — awaiting response", tone: "accent" },
    { opp: "created", int: null, label: "Not sent yet", tone: "warn" },
    { opp: "failed_send", int: null, label: "SMS failed", tone: "bad" },
    { opp: "disqualified_played", int: null, label: "Removed — played before send", tone: "neutral" },
    { opp: "skipped_cap", int: null, label: "Skipped — limit reached", tone: "neutral" },
    { opp: "skipped_cooldown", int: null, label: "Skipped — cooldown active", tone: "neutral" },
    { opp: "skipped_invalid_phone", int: null, label: "Skipped — no valid phone", tone: "warn" },
    { opp: "expired", int: null, label: "Expired with campaign", tone: "neutral" },
    { opp: "inactive", int: null, label: "Targeted", tone: "neutral" },
  ])("$opp / $int → $label", ({ opp, int, label, tone }) => {
    const result = customerOutcomePresentation({ opportunity_status: opp, intervention_status: int });
    expect(result.label).toBe(label);
    expect(result.tone).toBe(tone);
  });
});

describe("isCustomerConverted (responded ⇒ Converted rule)", () => {
  it("reports true only for a responded intervention", () => {
    expect(isCustomerConverted({ intervention_status: "responded" })).toBe(true);
    expect(isCustomerConverted({ intervention_status: "open" })).toBe(false);
    expect(isCustomerConverted({ intervention_status: "no_response" })).toBe(false);
    expect(isCustomerConverted({ intervention_status: null })).toBe(false);
  });

  it("drives the outcome decision table's Converted branch", () => {
    expect(
      customerOutcomePresentation({ opportunity_status: "sent", intervention_status: "responded" }).label,
    ).toBe("Converted");
  });
});