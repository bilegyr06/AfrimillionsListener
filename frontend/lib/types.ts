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
  // Enriched phase-3 drill-down fields: the SMS delivery outcome of the
  // accepted send plus the customer's qualifying activity inside the campaign
  // attribution window.
  delivery_status: string | null;
  qualifying_plays: number;
  attributed_amount: number;
  games_played: number;
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

// ---------------------------------------------------------------------------
// Statistics (campaign reporting surface)
// ---------------------------------------------------------------------------

// Aggregated campaign statistics served by /stats/campaigns and
// /stats/campaigns/{id}. Field shapes mirror app.services.statistics, which is
// the single source of truth for the report contract.

export interface CampaignAudienceStatus {
  rows: number;
  users: number;
}

export interface CampaignAudience {
  opportunities: number;
  unique_customers: number;
  pending_evaluation: number;
  not_sent_to: {
    disqualified_played: number;
    skipped_cap: number;
    skipped_cooldown: number;
    skipped_invalid_phone: number;
    failed_send: number;
    expired: number;
  };
  statuses: Record<string, CampaignAudienceStatus>;
}

export interface CampaignSmsPerformance {
  accepted: number;
  contacted_customers: number;
  unmatched: number;
  delivered: number;
  failed: number;
  rejected: number;
  expired: number;
  dnd: number;
  deferred: number;
  sent_awaiting_delivery: number;
  delivery_rate: number | null;
  cost: number;
  avg_cost_per_accepted: number | null;
}

export interface ResponseTiming {
  count: number;
  avg: number | null;
  median: number | null;
  p25: number | null;
  p75: number | null;
  min: number | null;
  max: number | null;
}

// Time-to-first-play buckets; keys match the backend's fixed bucket names.
export type ResponseBucketKey = "lt_1h" | "1h_to_6h" | "6h_to_12h" | "12h_to_24h" | "ge_24h";

export interface CampaignResponsePerformance {
  accepted_sms: number;
  contacted_customers: number;
  converted_customers: number;
  conversion_events: number;
  not_converted_customers: number;
  conversion_rate: number;
  pending_outcome: number;
  first_qualifying_play_at: string | null;
  time_to_first_play: ResponseTiming;
  buckets: Record<ResponseBucketKey, number>;
}

// Persistent player activity: a projection of the plays table joined onto the
// campaign's contacted audience. A play qualifies when it lies strictly after
// the customer's first Welcome SMS and at or before the attribution window end
// (ended_at for closed campaigns, now while active).
export interface CampaignActivity {
  window_end: string;
  qualifying_plays: number;
  players: number;
  single_play_players: number;
  repeat_players: number;
  converted_players: number;
  game_count: number;
  avg_plays_per_player: number | null;
  avg_plays_per_converted: number | null;
  avg_plays_per_contacted: number | null;
  repeat_rate: number | null;
  max_plays_per_player: number;
  total_play_amount: number;
  avg_play_amount: number | null;
  avg_amount_per_converted: number | null;
  avg_amount_per_contacted: number | null;
  before_sms: number;
  after_window: number;
}

export interface GameStats {
  game_name: string;
  plays: number;
  customers: number;
  amount: number;
  avg_amount: number;
}

export interface CampaignWindow {
  started_at: string;
  ended_at: string | null;
  attribution_end: string | null;
  description: string;
}

// Money/rate convention (shared by every reporting surface): a rate or unit
// cost whose denominator is zero is null ("unavailable"), never a fallback 0.
// "Activity/cost ratio" = attributed play amount / SMS cost for the campaign's
// own accepted Welcome SMS. It is a descriptive ratio of available data, not
// ROI, and total_play_amount is not revenue.
export interface CampaignEconomics {
  sms_cost: number;
  avg_cost_per_accepted: number | null;
  cost_per_contacted: number | null;
  cost_per_conversion: number | null;
  total_play_amount: number;
  play_amount_per_converted: number | null;
  play_amount_per_contacted: number | null;
  activity_cost_ratio: number | null;
}

// The conceptual conversion funnel. Stages are heterogeneous by design: they
// move from opportunities (logins) to distinct customers, then accepted sends,
// then delivery outcomes, then converted customers - so stages are not strict
// 1:1 drops over one population.
export interface CampaignFunnel {
  opportunities: number;
  unique_customers: number;
  accepted: number;
  delivered: number;
  converted_customers: number;
}

export interface CampaignStatisticsDetail {
  campaign: Campaign;
  window: CampaignWindow;
  audience: CampaignAudience;
  funnel: CampaignFunnel;
  sms: CampaignSmsPerformance;
  response: CampaignResponsePerformance;
  activity: CampaignActivity;
  games: GameStats[];
  economics: CampaignEconomics;
}

export interface CampaignStatisticsSummary {
  campaign_id: number;
  name: string | null;
  status: string;
  feature: string;
  started_at: string;
  ended_at: string | null;
  audience: {
    opportunities: number;
    unique_customers: number;
  };
  sms: {
    accepted: number;
    contacted_customers: number;
    delivered: number;
    deferred: number;
    cost: number;
  };
  response: {
    converted_customers: number;
    conversion_rate: number;
    avg_response_seconds: number | null;
    still_pending: number;
  };
  activity: {
    qualifying_plays: number;
    players: number;
    total_play_amount: number;
  };
  economics: CampaignEconomics;
}