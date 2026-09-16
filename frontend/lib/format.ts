// Formatting and label helpers. Business terminology lives here so pages stay
// declarative and the backend's raw values never leak into the UI.

const dateFmt = new Intl.DateTimeFormat(undefined, {
  day: "numeric",
  month: "short",
  year: "numeric",
});
const timeFmt = new Intl.DateTimeFormat(undefined, {
  hour: "2-digit",
  minute: "2-digit",
});
const dateTimeFmt = new Intl.DateTimeFormat(undefined, {
  day: "numeric",
  month: "short",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return dateTimeFmt.format(d);
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return dateFmt.format(d);
}

export function formatTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return timeFmt.format(d);
}

export function formatNumber(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return value.toLocaleString(undefined, { maximumFractionDigits: 0 });
}

export function formatMoney(amount: number | null | undefined, currency?: string | null): string {
  if (amount === null || amount === undefined) return "—";
  const formatted = amount.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return currency ? `${currency} ${formatted}` : formatted;
}

export function formatPercent(rate: number | null | undefined): string {
  if (rate === null || rate === undefined) return "—";
  return `${(rate * 100).toLocaleString(undefined, { maximumFractionDigits: 1 })}%`;
}

// For unit-cost-style ratios with at most two decimals (e.g. avg plays per
// player) where whole-number rounding would hide the ratio.
export function formatRatio(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return value.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "—";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const secs = Math.round(seconds % 60);
  if (hours > 0) return `${hours}h ${minutes}m`;
  if (minutes > 0) return `${minutes}m ${secs}s`;
  return `${secs}s`;
}

// ---------------------------------------------------------------------------
// Business labels
// ---------------------------------------------------------------------------

export type Tone = "ok" | "bad" | "warn" | "neutral" | "accent";

export interface StatusPresentation {
  label: string;
  tone: Tone;
}

const SMS_KIND_LABELS: Record<string, string> = {
  welcome: "Welcome",
  inactive: "Inactive",
  manual: "Manual",
};

export function smsKindLabel(kind: string): string {
  return SMS_KIND_LABELS[kind] ?? kind;
}

const SMS_STATUS_LABELS: Record<string, StatusPresentation> = {
  delivered: { label: "Delivered", tone: "ok" },
  sent: { label: "Sent (awaiting delivery)", tone: "accent" },
  failed: { label: "Failed", tone: "bad" },
  rejected: { label: "Rejected", tone: "bad" },
  expired: { label: "Expired", tone: "warn" },
  deferred: { label: "Deferred", tone: "neutral" },
  dnd: { label: "Blocked (DND)", tone: "warn" },
};

export function smsStatusPresentation(status: string): StatusPresentation {
  return SMS_STATUS_LABELS[status] ?? { label: status, tone: "neutral" };
}

// A customer's outcome is derived from the campaign row: the opportunity tells
// us whether a send was attempted/dropped, the intervention tells us whether a
// sent SMS converted.
export function customerOutcomePresentation(row: {
  opportunity_status: string;
  intervention_status: string | null;
}): StatusPresentation {
  const opp = row.opportunity_status;
  const int = row.intervention_status;

  if (int === "responded") return { label: "Converted", tone: "ok" };
  if (int === "no_response") return { label: "Sent, no conversion", tone: "neutral" };
  if (int === "open" || opp === "sent") return { label: "Sent — awaiting response", tone: "accent" };
  if (opp === "failed_send") return { label: "SMS failed", tone: "bad" };
  if (opp === "created") return { label: "Not sent yet", tone: "warn" };
  if (opp === "disqualified_played") return { label: "Removed — played before send", tone: "neutral" };
  if (opp === "skipped_cap") return { label: "Skipped — limit reached", tone: "neutral" };
  if (opp === "skipped_cooldown") return { label: "Skipped — cooldown active", tone: "neutral" };
  if (opp === "skipped_invalid_phone") return { label: "Skipped — no valid phone", tone: "warn" };
  if (opp === "expired") return { label: "Expired with campaign", tone: "neutral" };
  return { label: "Targeted", tone: "neutral" };
}

const FILE_STATUS_LABELS: Record<string, StatusPresentation> = {
  received: { label: "Processing", tone: "accent" },
  succeeded: { label: "Processed", tone: "ok" },
  failed: { label: "Failed", tone: "bad" },
};

export function fileStatusPresentation(status: string): StatusPresentation {
  return FILE_STATUS_LABELS[status] ?? { label: status, tone: "neutral" };
}

export const CAMPAIGN_STATUS_LABELS: Record<string, StatusPresentation> = {
  active: { label: "Active", tone: "ok" },
  closed: { label: "Closed", tone: "neutral" },
};

export function campaignStatusPresentation(status: string): StatusPresentation {
  return CAMPAIGN_STATUS_LABELS[status] ?? { label: status, tone: "neutral" };
}

export function campaignDisplayName(name: string | null, id: number): string {
  return name?.trim() ? name : `Campaign #${id}`;
}