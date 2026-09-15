// Typed view of the backend API responses the UI uses.
// Field shapes mirror the JSON returned by app.main / app.db.database.

export interface ApiErrorBody {
  message?: string;
}

// ---------------------------------------------------------------------------
// Report / overview
// ---------------------------------------------------------------------------

export interface SmsSummary {
  total: number;
  sent: number;
  delivered: number;
  failed: number;
  dnd: number;
  rejected: number;
  expired: number;
  deferred: number;
  total_cost: number;
  today: number;
}

export interface OpportunityCounts {
  created?: number;
  sent?: number;
  failed_send?: number;
  disqualified_played?: number;
  skipped_cap?: number;
  skipped_cooldown?: number;
  skipped_invalid_phone?: number;
  expired?: number;
  total: number;
}

export interface InterventionCounts {
  open?: number;
  responded?: number;
  no_response?: number;
  total: number;
}

export interface CampaignStats {
  campaign_id: number;
  opportunities: OpportunityCounts;
  interventions: InterventionCounts;
  response_rate: number | null;
  avg_response_seconds: number | null;
}

export interface Campaign {
  id: number;
  name: string | null;
  started_at: string;
  ended_at: string | null;
  status: "active" | "closed" | string;
  created_at: string;
  config?: Record<string, string> | null;
  // Backend tags campaign records with the feature they belong to (welcome).
  // Optional so the UI still renders against an older backend.
  feature?: string;
}

export interface WalletSnapshot {
  id: number;
  balance: number;
  currency: string;
  fetched_at: string;
}

export interface UploadedFile {
  id: number;
  original_filename: string;
  stored_filename: string;
  dataset: string;
  uploaded_at: string;
  uploaded_by: string | null;
  status: string;
  row_count: number | null;
  parse_error: string | null;
  processed_at: string | null;
}

// The backend is the single source of truth for which features are active.
// The UI renders this scope; it never derives it from settings or env.
export interface FeaturesScope {
  enabled_features: string[];
  active_kinds: string[];
  breakdown: Record<string, SmsSummary>;
}

export interface ReportOverview {
  phone_sms: SmsSummary;
  features: FeaturesScope;
  campaign: {
    active: boolean;
    total_campaigns: number;
    current: CampaignStats | null;
    feature?: string;
  };
  files: {
    total: number;
    failed: number;
    latest: UploadedFile | null;
  };
  wallet: WalletSnapshot | null;
}

// ---------------------------------------------------------------------------
// Campaigns
// ---------------------------------------------------------------------------

export interface CampaignWithStats {
  campaign: Campaign;
  stats: CampaignStats;
}

export interface CampaignCustomersResponse {
  campaign_id: number;
  items: CustomerRow[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
}

export interface CustomerRow {
  user_id: string;
  first_name: string | null;
  phone_raw: string | null;
  phone_normalized: string | null;
  login_at: string;
  opportunity_status: string;
  intervention_status: string | null;
  sent_at: string | null;
  play_at: string | null;
  response_seconds: number | null;
}

// ---------------------------------------------------------------------------
// SMS activity
// ---------------------------------------------------------------------------

export interface SmsLogEntry {
  id: number;
  message_id: string | null;
  user_id: string;
  kind: string;
  phone: string;
  status: string;
  cost: number;
  balance_after: number | null;
  cycle_id: string | null;
  sent_at: string;
}

export interface Paged<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
}

// The default /sms/logs response carries the backend's active feature scope
// so the UI can derive the kind filter from a single source.
export interface SmsLogsResponse extends Paged<SmsLogEntry> {
  enabled_features: string[];
}

// ---------------------------------------------------------------------------
// Files registry
// ---------------------------------------------------------------------------

export interface FilesResponse {
  items: UploadedFile[];
}

export interface UploadResponse {
  message: string;
  record: UploadedFile;
}

// ---------------------------------------------------------------------------
// Settings
// ---------------------------------------------------------------------------

export type SettingInputType = "text" | "textarea" | "number" | "time" | "checkbox" | "tags";

export interface SettingRow {
  key: string;
  label: string;
  description: string;
  input_type: SettingInputType;
  kind: string;
  value: string;
}

export interface SettingsResponse {
  items: SettingRow[];
}

export interface SettingUpdateResponse {
  message: string;
  setting: { key: string; label: string; value: string };
}

// ---------------------------------------------------------------------------
// Lifecycle actions
// ---------------------------------------------------------------------------

export interface CampaignLifecycleResult {
  campaign: Campaign;
  stats: CampaignStats;
  closed_campaign: { campaign: Campaign; stats: CampaignStats } | null;
}

export interface CloseCampaignResult {
  campaign: Campaign;
  stats: CampaignStats;
}

export interface HealthResponse {
  status: string;
}