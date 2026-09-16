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
  formatRatio,
  smsKindLabel,
} from "@/lib/format";
import type {
  Campaign,
  CampaignStats,
  CampaignStatisticsDetail,
  ReportOverview,
} from "@/lib/types";

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

const BUCKET_LABELS: Array<[string, string]> = [
  ["lt_1h", "Under 1 hour"],
  ["1h_to_6h", "1 \u2013 6 hours"],
  ["6h_to_12h", "6 \u2013 12 hours"],
  ["12h_to_24h", "12 \u2013 24 hours"],
  ["ge_24h", "24 hours or more"],
];

export function buildStatisticsSnapshot(detail: CampaignStatisticsDetail): string {
  const { campaign, window, audience, funnel, sms, response, activity, games, economics } =
    detail;
  const lines: string[] = [];

  lines.push("AFRIMILLIONS LISTENER \u2014 CAMPAIGN STATISTICS REPORT");
  lines.push(`Generated ${formatDateTime(new Date().toISOString())}.`);
  lines.push("");
  lines.push(`Campaign: ${campaignDisplayName(campaign.name, campaign.id)}`);
  lines.push(`Status: ${campaign.status === "active" ? "Active" : "Closed"}`);
  lines.push(
    `Period: ${formatDate(campaign.started_at)} \u2192 ${campaign.ended_at ? formatDate(campaign.ended_at) : "present"}`,
  );
  lines.push(`Attribution window: ${window.description}`);
  lines.push("");

  heading(lines, "CAMPAIGN");
  lines.push(`  Opportunities (logins)          ${formatNumber(funnel.opportunities)}`);
  lines.push(`  Unique customers                ${formatNumber(funnel.unique_customers)}`);
  lines.push(`  Accepted (SMS sent)             ${formatNumber(funnel.accepted)}`);
  lines.push(`  Delivered                       ${formatNumber(funnel.delivered)}`);
  lines.push(`  Converted customers             ${formatNumber(funnel.converted_customers)}`);
  lines.push(`  Conversion rate                 ${formatPercent(response.conversion_rate)}`);
  lines.push("");
  lines.push(
    "Stages move from logins to distinct customers to accepted sends to delivery",
  );
  lines.push("outcomes to conversions; a customer can appear in several stages.");
  lines.push("");

  heading(lines, "AUDIENCE");
  lines.push(`  Opportunities (logins)          ${formatNumber(audience.opportunities)}`);
  lines.push(`  Unique customers                ${formatNumber(audience.unique_customers)}`);
  lines.push(`  Pending evaluation              ${formatNumber(audience.pending_evaluation)}`);
  lines.push("  Not sent to:");
  lines.push(`    Removed \u2014 played early         ${formatNumber(audience.not_sent_to.disqualified_played)}`);
  lines.push(`    Skipped \u2014 limit reached         ${formatNumber(audience.not_sent_to.skipped_cap)}`);
  lines.push(`    Skipped \u2014 cooldown active       ${formatNumber(audience.not_sent_to.skipped_cooldown)}`);
  lines.push(`    Skipped \u2014 no valid phone        ${formatNumber(audience.not_sent_to.skipped_invalid_phone)}`);
  lines.push(`    SMS failed to send              ${formatNumber(audience.not_sent_to.failed_send)}`);
  lines.push(`    Expired with campaign           ${formatNumber(audience.not_sent_to.expired)}`);
  lines.push("");

  heading(lines, "CUSTOMER RESPONSE");
  lines.push(`  Converted customers             ${formatNumber(response.converted_customers)}`);
  lines.push(`  Conversion rate                 ${formatPercent(response.conversion_rate)}`);
  lines.push(`  Conversion events               ${formatNumber(response.conversion_events)}`);
  lines.push(`  Customers not converted         ${formatNumber(response.not_converted_customers)}`);
  lines.push(`  Still awaiting response         ${formatNumber(response.pending_outcome)}`);
  lines.push(
    `  First qualifying play           ${response.first_qualifying_play_at ? formatDateTime(response.first_qualifying_play_at) : "\u2014"}`,
  );
  lines.push(`  Avg time to first play          ${formatDuration(response.time_to_first_play.avg)}`);
  lines.push(`  Median time to first play       ${formatDuration(response.time_to_first_play.median)}`);
  lines.push(`  P25 / P75                       ${formatDuration(response.time_to_first_play.p25)} / ${formatDuration(response.time_to_first_play.p75)}`);
  lines.push("");
  lines.push("  Time to first qualifying play:");
  for (const [key, label] of BUCKET_LABELS) {
    lines.push(`    ${label.padEnd(22)} ${formatNumber(response.buckets[key as keyof typeof response.buckets] ?? 0)}`);
  }
  lines.push("");

  heading(lines, "PLAYER ACTIVITY");
  lines.push(`  Qualifying plays                 ${formatNumber(activity.qualifying_plays)}`);
  lines.push(`  Players                          ${formatNumber(activity.players)}`);
  lines.push(`  Games played                     ${formatNumber(activity.game_count)}`);
  lines.push(`  Played once                      ${formatNumber(activity.single_play_players)}`);
  lines.push(`  Repeat players                   ${formatNumber(activity.repeat_players)}`);
  lines.push(`  Converted players                ${formatNumber(activity.converted_players)}`);
  lines.push(`  Avg plays per player             ${formatRatio(activity.avg_plays_per_player)}`);
  lines.push(`  Avg plays per converted          ${formatRatio(activity.avg_plays_per_converted)}`);
  lines.push(`  Avg plays per contacted          ${formatRatio(activity.avg_plays_per_contacted)}`);
  lines.push(`  Repeat share                     ${formatPercent(activity.repeat_rate)}`);
  lines.push(`  Most plays by one player         ${formatNumber(activity.max_plays_per_player)}`);
  lines.push(`  Total play amount                ${formatMoney(activity.total_play_amount)}`);
  lines.push(`  Average amount per play          ${formatMoney(activity.avg_play_amount)}`);
  lines.push(`  Avg amount per converted         ${formatMoney(activity.avg_amount_per_converted)}`);
  lines.push(`  Avg amount per contacted         ${formatMoney(activity.avg_amount_per_contacted)}`);
  lines.push(`  Played before SMS                ${formatNumber(activity.before_sms)}`);
  lines.push(`  Played after window              ${formatNumber(activity.after_window)}`);
  lines.push("");

  heading(lines, "GAME ACTIVITY");
  if (games.length === 0) {
    lines.push("  No qualifying plays to rank.");
  } else {
    for (const g of games) {
      lines.push(
        `  ${g.game_name.padEnd(20)} ${formatNumber(g.plays).padStart(6)} plays \u00b7 ${formatNumber(g.customers).padStart(4)} players \u00b7 ${formatMoney(g.amount)} \u00b7 avg ${formatMoney(g.avg_amount)}`,
      );
    }
  }
  lines.push("");

  heading(lines, "SMS PERFORMANCE");
  lines.push(`  Accepted (sent)                 ${formatNumber(sms.accepted)}`);
  lines.push(`  Customers contacted             ${formatNumber(sms.contacted_customers)}`);
  lines.push(`  Delivered                       ${formatNumber(sms.delivered)}`);
  lines.push(`  Failed                          ${formatNumber(sms.failed)}`);
  lines.push(`  Rejected                        ${formatNumber(sms.rejected)}`);
  lines.push(`  Blocked (DND)                   ${formatNumber(sms.dnd)}`);
  lines.push(`  Expired                         ${formatNumber(sms.expired)}`);
  lines.push(`  Deferred (no provider call)     ${formatNumber(sms.deferred)}`);
  lines.push(`  Awaiting delivery               ${formatNumber(sms.sent_awaiting_delivery)}`);
  lines.push(`  Unmatched to delivery log       ${formatNumber(sms.unmatched)}`);
  lines.push(`  Delivery rate                   ${formatPercent(sms.delivery_rate)}`);
  lines.push(`  Avg cost per accepted SMS       ${formatMoney(economics.avg_cost_per_accepted)}`);
  lines.push("");

  heading(lines, "CAMPAIGN ECONOMICS");
  lines.push(`  SMS cost                        ${formatMoney(economics.sms_cost)}`);
  lines.push(`  Cost per contacted customer     ${formatMoney(economics.cost_per_contacted)}`);
  lines.push(`  Cost per conversion             ${formatMoney(economics.cost_per_conversion)}`);
  lines.push(`  Attributed play amount          ${formatMoney(economics.total_play_amount)}`);
  lines.push(`  Play amount per converted       ${formatMoney(economics.play_amount_per_converted)}`);
  lines.push(`  Play amount per contacted       ${formatMoney(economics.play_amount_per_contacted)}`);
  lines.push(`  Activity / cost ratio           ${formatRatio(economics.activity_cost_ratio)}`);
  lines.push("");
  lines.push(
    "SMS cost covers this campaign's own accepted Welcome SMS. Attributed play amount",
  );
  lines.push("is qualifying plays inside the attribution window \u2014 it is not revenue, and the");
  lines.push(
    "activity/cost ratio is a descriptive ratio of available data, not ROI. A \u2014 marks a",
  );
  lines.push("metric with no base to divide over, not a zero result.");
  return lines.join("\n");
}