// Plain-text report builders for the operator snapshot/export feature.
// They assemble the numbers already shown on the page into a single,
// copy-paste friendly block for external reports.

import {
  campaignDisplayName,
  formatDate,
  formatDateTime,
  formatDuration,
  formatMoney,
  formatNumber,
  formatPercent,
  smsKindLabel,
} from "@/lib/format";
import type { Campaign, CampaignStats, ReportOverview } from "@/lib/types";

const FEATURE_LABELS: Record<string, string> = {
  welcome: "Welcome SMS",
  inactive: "Inactivity SMS",
};

export function featureLabel(kind: string): string {
  return FEATURE_LABELS[kind] ?? smsKindLabel(kind);
}

export function snapshotFilename(slug: string, date: Date = new Date()): string {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  return `${slug}-${y}-${m}-${d}.txt`;
}

function heading(lines: string[], text: string): void {
  lines.push(text);
  lines.push("".padEnd(text.length, "\u2013"));
}

export function buildOverviewSnapshot(overview: ReportOverview): string {
  const lines: string[] = [];
  const sms = overview.phone_sms;
  const enabled = overview.features.enabled_features;
  const breakdown = overview.features.breakdown ?? {};

  lines.push("AFRIMILLIONS LISTENER \u2014 STATISTICS SNAPSHOT");
  lines.push(`Generated ${formatDateTime(new Date().toISOString())}.`);
  lines.push("");
  lines.push(
    `Active features: ${enabled.length ? enabled.map(featureLabel).join(", ") : "None"}`,
  );
  lines.push("");

  heading(lines, "SMS traffic \u2014 all time");
  lines.push(`  Total messages              ${formatNumber(sms.total)}`);
  lines.push(`  Delivered                   ${formatNumber(sms.delivered)}`);
  lines.push(`  Sent (awaiting delivery)    ${formatNumber(sms.sent - sms.delivered)}`);
  lines.push(`  Failed                      ${formatNumber(sms.failed)}`);
  lines.push(`  Rejected                    ${formatNumber(sms.rejected)}`);
  lines.push(`  Deferred                    ${formatNumber(sms.deferred)}`);
  lines.push(`  Expired                     ${formatNumber(sms.expired)}`);
  lines.push(`  Blocked (DND)               ${formatNumber(sms.dnd)}`);
  lines.push(`  Sent today                  ${formatNumber(sms.today)}`);
  lines.push(`  Total cost                  ${formatMoney(sms.total_cost)}`);
  lines.push("");

  const kinds = Object.keys(breakdown).sort();
  if (kinds.length > 0) {
    heading(lines, "Per-feature SMS breakdown \u2014 all time");
    for (const kind of kinds) {
      const b = breakdown[kind];
      lines.push(`  ${featureLabel(kind)}`);
      lines.push(`    Delivered               ${formatNumber(b.delivered)}`);
      lines.push(`    Failed                  ${formatNumber(b.failed)}`);
      lines.push(`    Sent today              ${formatNumber(b.today)}`);
      lines.push(`    Cost                    ${formatMoney(b.total_cost)}`);
    }
    lines.push("");
  }

  const campaign = overview.campaign;
  heading(lines, "Campaign");
  if (campaign.active && campaign.current) {
    const c = campaign.current;
    lines.push(`  Active campaign             #${c.campaign_id}`);
    lines.push(`  Customers targeted          ${formatNumber(c.opportunities.total)}`);
    lines.push(`  SMS sent                    ${formatNumber(c.interventions.total)}`);
    lines.push(`  SMS failed                  ${formatNumber(c.opportunities.failed_send ?? 0)}`);
    lines.push(`  Conversions                 ${formatNumber(c.interventions.responded ?? 0)}`);
    lines.push(`  Conversion rate             ${formatPercent(c.response_rate)}`);
    lines.push(
      `  Avg time to convert        ${c.avg_response_seconds != null ? formatDuration(c.avg_response_seconds) : "\u2014"}`,
    );
    lines.push(`  Still pending evaluation    ${formatNumber(c.opportunities.created ?? 0)}`);
  } else {
    lines.push("  No campaign is active right now.");
  }
  lines.push("");

  heading(lines, "Wallet balance");
  if (overview.wallet) {
    lines.push(`  ${formatMoney(overview.wallet.balance, overview.wallet.currency)}`);
    lines.push(`  Recorded ${formatDateTime(overview.wallet.fetched_at)}`);
  } else {
    lines.push("  No balance snapshot on record.");
  }
  lines.push("");

  lines.push(
    "All-time figures cover currently enabled features plus manual operator sends;",
  );
  lines.push("they do not include records of disabled features. Sending today resets daily.");
  return lines.join("\n");
}

export function buildCampaignSnapshot(campaign: Campaign, stats: CampaignStats): string {
  const lines: string[] = [];
  const opps = stats.opportunities;
  const ints = stats.interventions;

  lines.push("AFRIMILLIONS LISTENER \u2014 CAMPAIGN REPORT");
  lines.push(`Generated ${formatDateTime(new Date().toISOString())}.`);
  lines.push("");
  lines.push(`Campaign: ${campaignDisplayName(campaign.name, campaign.id)}`);
  lines.push(`Feature: ${featureLabel(campaign.feature ?? "welcome")}`);
  lines.push(`Status: ${campaign.status === "active" ? "Active" : "Closed"}`);
  lines.push(
    `Period: ${formatDate(campaign.started_at)} \u2192 ${campaign.ended_at ? formatDate(campaign.ended_at) : "present"}`,
  );
  lines.push("");

  heading(lines, "Results");
  lines.push(`  Customers targeted          ${formatNumber(opps.total)}`);
  lines.push(`  SMS sent                    ${formatNumber(ints.total)}`);
  lines.push(`  SMS failed                  ${formatNumber(opps.failed_send ?? 0)}`);
  lines.push(`  Conversions                 ${formatNumber(ints.responded ?? 0)}`);
  lines.push(`  Conversion rate             ${formatPercent(stats.response_rate)}`);
  lines.push(
    `  Avg time to convert        ${stats.avg_response_seconds != null ? formatDuration(stats.avg_response_seconds) : "\u2014"}`,
  );
  lines.push(`  Still awaiting evaluation   ${formatNumber(opps.created ?? 0)}`);
  lines.push(`  No response                 ${formatNumber(ints.no_response ?? 0)}`);
  lines.push("");
  heading(lines, "Customers not sent to");
  lines.push(`  Removed \u2014 played before send   ${formatNumber(opps.disqualified_played ?? 0)}`);
  lines.push(`  Skipped \u2014 cap reached          ${formatNumber(opps.skipped_cap ?? 0)}`);
  lines.push(`  Skipped \u2014 cooldown active      ${formatNumber(opps.skipped_cooldown ?? 0)}`);
  lines.push(`  Skipped \u2014 no valid phone       ${formatNumber(opps.skipped_invalid_phone ?? 0)}`);
  lines.push(`  Expired with campaign          ${formatNumber(opps.expired ?? 0)}`);
  lines.push("");
  lines.push(
    "Conversion rate is the share of SMS-recipient responders; customers who were not sent to",
  );
  lines.push("are excluded from the rate but listed above.");
  return lines.join("\n");
}