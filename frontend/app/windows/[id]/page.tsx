"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import Notice from "@/components/notice";
import StatusPill from "@/components/status-pill";
import { Metric, MetricStrip, MetricTier } from "@/components/statistics/metric";
import StatSection from "@/components/statistics/section";
import CampaignAttribution from "@/components/windows/campaign-attribution";
import ControlConfiguration from "@/components/windows/control-configuration";
import ExportEvaluation from "@/components/windows/export-evaluation";
import LifecycleControls from "@/components/windows/lifecycle-controls";
import SnapshotCell from "@/components/windows/snapshot-cell";
import SplitSection from "@/components/windows/split-section";
import StatusBanner from "@/components/windows/status-banner";
import { Tabs, TabPanel } from "@/components/windows/tabs";
import { ErrorBlock, Loading } from "@/components/state-ui";
import { apiGet, apiPost } from "@/lib/api";
import {
  assignmentMethodLabel,
  datasetLabel,
  formatDate,
  formatDateTime,
  formatMoney,
  formatNumber,
  formatPercent,
  formatRatio,
  reportStatePresentation,
  runStatusPresentation,
  segmentLabel,
  windowDisplayName,
  windowStatusPresentation,
} from "@/lib/format";
import { dispatchResultText, evaluateResultText } from "@/lib/window-actions";
import type {
  DispatchResult,
  EvaluateRunResponse,
  SegmentsResponse,
  WindowDataState,
  WindowDetail,
  WindowGroupMetrics,
  WindowReport,
} from "@/lib/types";
import { useQuery } from "@/lib/use-query";

function useNow(intervalMs = 60000) {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}

type Flash = { kind: "success" | "error"; text: string };

// ---------------------------------------------------------------------------
// Configuration + split
// ---------------------------------------------------------------------------

function ConfigSection({ window }: { window: WindowDetail }) {
  return (
    <StatSection title="Window">
      <div className="section-body">
        <table className="kv">
          <tbody>
            <tr>
              <th>Status</th>
              <td>{window.status}</td>
            </tr>
            <tr>
              <th>Period</th>
              <td>
                {formatDateTime(window.start_time)}
                {"\u2009\u2192\u2009"}
                {formatDateTime(window.end_time)}
              </td>
            </tr>
            <tr>
              <th>Finalization</th>
              <td>
                {window.finalized_at
                  ? `Frozen ${formatDateTime(window.finalized_at)}`
                  : `Deadline ${formatDateTime(window.finalization_deadline)}`}
              </td>
            </tr>
            <tr>
              <th>Time zone</th>
              <td>{window.business_timezone}</td>
            </tr>
            <tr>
              <th>Assignment mode</th>
              <td>{assignmentMethodLabel(window.assignment_method)}</td>
            </tr>
            <tr>
              <th>Eligible count (N)</th>
              <td>{formatNumber(window.eligible_count)}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <div className="section-body flush">
        <MetricStrip>
          <Metric label="Eligible (N)" value={formatNumber(window.eligible_count)} />
          <Metric label="Campaign" value={formatNumber(window.audience.campaign)} />
          <Metric label="Control" value={formatNumber(window.audience.control)} />
          <Metric label="Assigned total" value={formatNumber(window.audience.total)} />
        </MetricStrip>
      </div>
    </StatSection>
  );
}

// ---------------------------------------------------------------------------
// Runs (operator surface)
// ---------------------------------------------------------------------------

function RunsSection({
  window,
  catalog,
  onChanged,
  flash,
}: {
  window: WindowDetail;
  catalog?: SegmentsResponse["items"];
  onChanged: () => void;
  flash: (f: Flash) => void;
}) {
  const [note, setNote] = useState("");
  const [selected, setSelected] = useState<string[]>(["unsegmented"]);
  const [starting, setStarting] = useState(false);
  const [busyRun, setBusyRun] = useState<number | null>(null);
  const [result, setResult] = useState<{
    runId: number;
    kind: "start" | "dispatch";
    text: string;
  } | null>(null);

  interface StartRunResponse {
  run: { id: number };
  evaluation?: {
    candidates: number;
    audience: { added: number; campaign: number; control: number; existing: number; invalid_phone: number };
    eligible_count: number | null;
  };
}

  function toggleSegment(id: string) {
    setSelected((current) =>
      current.includes(id) ? current.filter((s) => s !== id) : [...current, id],
    );
  }

  async function startRun() {
    if (selected.length === 0) {
      flash({ kind: "error", text: "Select at least one segment for this run." });
      return;
    }
    setStarting(true);
    try {
      const res = await apiPost<StartRunResponse>(`/windows/${window.id}/runs`, {
        segments: selected,
        note: note.trim() === "" ? null : note.trim(),
      });
      // The run now includes evaluation results from the atomic start
      const evalResult = res.evaluation;
      if (evalResult) {
        setResult({
          runId: res.run.id,
          kind: "start",
          text: `Evaluated ${evalResult.candidates} candidates. Added ${evalResult.audience.added} (${evalResult.audience.campaign} Campaign / ${evalResult.audience.control} Control); ${evalResult.audience.existing} already present; ${evalResult.audience.invalid_phone} invalid phones. Eligible count is now ${evalResult.eligible_count !== null ? evalResult.eligible_count : "unchanged"}.`,
        });
      } else {
        setResult({
          runId: res.run.id,
          kind: "start",
          text: "Run started with a frozen snapshot of the current data.",
        });
      }
      flash({ kind: "success", text: "Run started — target audience frozen." });
      setNote("");
      onChanged();
    } catch (err) {
      flash({ kind: "error", text: err instanceof Error ? err.message : "Could not start a run." });
    } finally {
      setStarting(false);
    }
  }

  async function act(runId: number, action: "dispatch" | "stop" | "complete") {
    setBusyRun(runId);
    setResult(null);
    try {
      if (action === "dispatch") {
        const res = await apiPost<DispatchResult>(`/runs/${runId}/dispatch`);
        setResult({ runId, kind: "dispatch", text: dispatchResultText(res) });
      } else {
        await apiPost(`/runs/${runId}/${action}`);
        flash({ kind: "success", text: `Run ${action} accepted.` });
      }
      onChanged();
    } catch (err) {
      flash({ kind: "error", text: err instanceof Error ? err.message : `Could not ${action} the run.` });
    } finally {
      setBusyRun(null);
    }
  }

  return (
    <StatSection title="Runs">
      {window.status === "active" && (
        <div className="section-body">
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-start" }}>
            <input
              className="input"
              value={note}
              placeholder="Optional note for this run"
              onChange={(e) => setNote(e.target.value)}
              style={{ flex: "1 1 240px" }}
            />
            <button className="btn btn-primary" disabled={starting} onClick={startRun}>
              {starting ? "Starting..." : "Start run"}
            </button>
          </div>
          <div className="segment-picker">
            <span className="segment-picker-label">Segment scope</span>
            {(catalog ?? []).map((segment) => (
              <label key={segment.id} className="checkbox-row">
                <input
                  type="checkbox"
                  checked={selected.includes(segment.id)}
                  onChange={() => toggleSegment(segment.id)}
                />
                <span>
                  {segment.label}
                  {segment.default ? <span className="muted"> default</span> : null}
                </span>
              </label>
            ))}
            <span className="hint">
              This run&apos;s eligibility is limited to its selected segments.
            </span>
          </div>
        </div>
      )}

      {result && (
        <div className="section-body">
          <Notice kind="success" dismissMs={12000}>
            <strong>Run #{result.runId}</strong> {result.text}
          </Notice>
        </div>
      )}

      <div className="section-body flush">
        <table className="table">
          <thead>
            <tr>
              <th>Run</th>
              <th>Status</th>
              <th>Started</th>
              <th>Ended</th>
              <th>Note</th>
              <th>Segments</th>
              <th>Frozen snapshot</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {window.runs.map((run) => {
              const status = runStatusPresentation(run.status);
              const running = run.status === "running";
              return (
                <tr key={run.id}>
                  <td className="small">Run #{run.id}</td>
                  <td>
                    <StatusPill {...status} />
                  </td>
                  <td className="small muted">{formatDateTime(run.started_at)}</td>
                  <td className="small muted">{formatDateTime(run.ended_at)}</td>
                  <td className="small muted">{run.note ?? "\u2014"}</td>
                  <td className="small">
                    {run.selected_segments.map((id) => segmentLabel(id, catalog)).join(", ")}
                  </td>
                  <td className="small">
                    <SnapshotCell capturedAt={run.snapshot.captured_at} files={run.snapshot.files} />
                  </td>
                  <td className="small">
                    {running ? (
                      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                        <button
                          className="btn btn-primary btn-sm"
                          disabled={busyRun !== null}
                          onClick={() => act(run.id, "dispatch")}
                        >
                          {busyRun === run.id ? "Dispatching..." : "Dispatch SMS"}
                        </button>
                        <button
                          className="btn btn-danger-secondary btn-sm"
                          disabled={busyRun !== null}
                          onClick={() => act(run.id, "stop")}
                        >
                          Stop
                        </button>
                        <button
                          className="btn btn-secondary btn-sm"
                          disabled={busyRun !== null}
                          onClick={() => act(run.id, "complete")}
                        >
                          Complete
                        </button>
                      </div>
                    ) : (
                      <span className="muted">Completed</span>
                    )}
                  </td>
                </tr>
              );
            })}
            {window.runs.length === 0 ? (
              <tr>
                <td colSpan={8} className="small muted">
                  No runs started for this window yet.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
        <div className="section-body">
          <p className="hint" style={{ margin: 0 }}>
            Runs use a snapshot of data at start. Later uploads do not affect the run's target.
          </p>
        </div>
      </div>
    </StatSection>
  );
}

// ---------------------------------------------------------------------------
// Source data state
// ---------------------------------------------------------------------------

function DataStateSection({ windowId, status }: { windowId: number; status: string }) {
  const state = useQuery<WindowDataState>(() => apiGet(`/windows/${windowId}/data-state`), [windowId], 30000);

  return (
    <StatSection title="Source data">
      {state.loading && !state.data ? (
        <div className="section-body">
          <Loading text={"Loading data state..."} />
        </div>
      ) : state.error ? (
        <div className="section-body">
          <ErrorBlock message="We couldn't load the source data state." onRetry={state.reload} />
        </div>
      ) : state.data ? (
        <div className="section-body flush">
          <table className="table">
            <thead>
              <tr>
                <th>Dataset</th>
                <th>File</th>
                <th>Rows</th>
              </tr>
            </thead>
            <tbody>
              {state.data.current_files.map((file) => (
                <tr key={`${file.dataset}-${file.filename}`}>
                  <td className="small">{datasetLabel(file.dataset)}</td>
                  <td className="small">{file.filename}</td>
                  <td className="small num">{formatNumber(file.row_count)}</td>
                </tr>
              ))}
              {state.data.current_files.length === 0 ? (
                <tr>
                  <td colSpan={3} className="small muted">
                    No source files present right now. Upload data to make it available to future runs.
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
          <div className="section-body">
            <p className="hint" style={{ margin: 0 }}>
              Captured {formatDateTime(state.data.captured_at)}. Files shown here are available to a
              new run; existing runs stay bound to the snapshot they captured at start.
            </p>
          </div>
        </div>
      ) : null}
      {status === "finalized" && (
        <div className="section-body">
          <p className="hint" style={{ margin: 0 }}>
            Window finalized — the report is frozen and ignores everything uploaded after
            finalization.
          </p>
        </div>
      )}
    </StatSection>
  );
}

// ---------------------------------------------------------------------------
// Report section (Campaign vs Control) — backend-computed, never recomputed
// ---------------------------------------------------------------------------

function GroupMetrics({ group, title }: { group: WindowGroupMetrics; title: string }) {
  const pct = (v: number | null | undefined) => formatPercent(v);
  return (
    <StatSection title={title}>
      <div className="section-body flush">
        <MetricStrip>
          <Metric label="Audience" value={formatNumber(group.total_targeted_audience)} />
          <Metric label="Played" value={formatNumber(group.total_played_users)} />
          <Metric label="Deposited" value={formatNumber(group.total_deposited_users)} />
          <Metric label="Logged in" value={formatNumber(group.total_logged_in_users)} />
        </MetricStrip>
        <MetricTier>
          <Metric label="Total sales" value={formatMoney(group.total_sales)} accent />
          <Metric label="Total plays" value={formatNumber(group.total_plays)} />
          <Metric label="ARPU" value={formatMoney(group.arpu)} />
          <Metric label="ARPPU" value={formatMoney(group.arppu)} />
        </MetricTier>
      </div>
      <div className="section-body">
        <table className="kv">
          <tbody>
            <tr>
              <th>Login rate</th>
              <td>{pct(group.login_rate)}</td>
            </tr>
            <tr>
              <th>Play rate</th>
              <td>{pct(group.play_rate)}</td>
            </tr>
            <tr>
              <th>Deposit rate</th>
              <td>{pct(group.deposit_rate)}</td>
            </tr>
            <tr>
              <th>Plays per player</th>
              <td>{formatRatio(group.plays_per_player)}</td>
            </tr>
            <tr>
              <th>Active days per player</th>
              <td>{formatRatio(group.active_days_per_player)}</td>
            </tr>
            <tr>
              <th>Multi-day players</th>
              <td>{formatNumber(group.multi_day_players)}</td>
            </tr>
            <tr>
              <th>Multi-day player rate</th>
              <td>{pct(group.multi_day_player_rate)}</td>
            </tr>
            <tr>
              <th>Deposit-to-play rate</th>
              <td>{pct(group.deposit_to_play_rate)}</td>
            </tr>
            <tr>
              <th>Login-to-play rate</th>
              <td>{pct(group.login_to_play_rate)}</td>
            </tr>
            <tr>
              <th>Deposited, no play</th>
              <td>{formatNumber(group.deposited_no_play_users)}</td>
            </tr>
            <tr>
              <th>Logged in, no play</th>
              <td>{formatNumber(group.logged_in_no_play_users)}</td>
            </tr>
            <tr>
              <th>Single-play players</th>
              <td>{formatNumber(group.single_play_players)}</td>
            </tr>
            <tr>
              <th>Multiple-play players</th>
              <td>{formatNumber(group.multiple_play_players)}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </StatSection>
  );
}

function RunContributionsTable({ report }: { report: WindowReport }) {
  return (
    <StatSection title="Run contributions">
      <div className="section-body flush">
        <table className="table">
          <thead>
            <tr>
              <th>Run</th>
              <th>Status</th>
              <th>Target</th>
              <th>Accepted SMS</th>
              <th>Contacted</th>
              <th>Converted</th>
              <th>Attributed plays</th>
              <th>Attributed amount</th>
            </tr>
          </thead>
          <tbody>
            {report.runs.map((r) => {
              const status = runStatusPresentation(r.status);
              return (
                <tr key={r.run_id}>
                  <td className="small">Run #{r.run_id}</td>
                  <td>
                    <StatusPill {...status} />
                  </td>
                  <td className="small num">{formatNumber(r.targeted_users)}</td>
                  <td className="small num">{formatNumber(r.accepted_interventions)}</td>
                  <td className="small num">{formatNumber(r.contacted_users)}</td>
                  <td className="small num">{formatNumber(r.converted_users)}</td>
                  <td className="small num">{formatNumber(r.attributed_plays)}</td>
                  <td className="small num">{formatMoney(r.attributed_amount)}</td>
                </tr>
              );
            })}
            {report.runs.length === 0 ? (
              <tr>
                <td colSpan={8} className="small muted">
                  No Campaign Runs started for this window.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
    </StatSection>
  );
}

function ReportSection({ report }: { report: WindowReport }) {
  const windowStatus = windowStatusPresentation(report.window.status);
  const reportState = reportStatePresentation(report.window.status === "finalized" ? "frozen" : "live");
  return (
    <>
      <StatSection title="Campaign vs Control report">
        <div className="section-body">
          <table className="kv">
            <tbody>
              <tr>
                <th>Status</th>
                <td>
                  <StatusPill {...windowStatus} /> <StatusPill {...reportState} />
                </td>
              </tr>
              <tr>
                <th>Evaluation period</th>
                <td>
                  {formatDateTime(report.evaluation_period.start)}
                  {"\u2009\u2192\u2009"}
                  {formatDateTime(report.evaluation_period.end)}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <div className="section-body flush">
          <MetricStrip>
            <Metric label="Campaignable" value={formatNumber(report.audience.campaignable_total)} />
            <Metric label="Campaign" value={formatNumber(report.audience.campaign)} />
            <Metric label="Control" value={formatNumber(report.audience.control)} />
            <Metric label="Attributed amount" value={formatMoney(report.attribution.attributed_amount)} />
          </MetricStrip>
        </div>
      </StatSection>

      <CampaignAttribution group={report.groups.campaign} />

      <GroupMetrics group={report.groups.campaign} title="Campaign group" />
      <GroupMetrics group={report.groups.control} title="Control group" />

      <StatSection title="Games">
        {(["campaign", "control"] as const).map((kind) => {
          const games = report.games[kind];
          return (
            <div className="section-body flush" key={`games-${kind}`}>
              <h3 className="group-title">
                {kind} group
              </h3>
              <table className="table">
                <thead>
                  <tr>
                    <th>Game</th>
                    <th>Plays</th>
                    <th>Customers</th>
                    <th>Amount</th>
                    <th>Avg amount</th>
                  </tr>
                </thead>
                <tbody>
                  {games.map((g) => (
                    <tr key={g.game_name}>
                      <td className="small">{g.game_name}</td>
                      <td className="small num">{formatNumber(g.plays)}</td>
                      <td className="small num">{formatNumber(g.customers)}</td>
                      <td className="small num">{formatMoney(g.amount)}</td>
                      <td className="small num">{formatMoney(g.avg_amount)}</td>
                    </tr>
                  ))}
                  {games.length === 0 ? (
                    <tr>
                      <td colSpan={5} className="small muted">
                        No plays in this group.
                      </td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </div>
          );
        })}
      </StatSection>

      <RunContributionsTable report={report} />
    </>
  );
}

// ---------------------------------------------------------------------------
// Tab components
// ---------------------------------------------------------------------------

function OverviewTab({ window, catalog, now }: { window: WindowDetail; catalog?: SegmentsResponse["items"]; now: Date }) {
  const split = window.split.actual;
  const splitText =
    split.total_users > 0
      ? `${formatNumber(split.campaign_users)} / ${formatNumber(split.control_users)}`
      : "No audience yet";
  const runningCount = window.runs.filter((r) => r.status === "running").length;
  const runsMetricText =
    runningCount > 0
      ? `${formatNumber(window.runs.length)} (${formatNumber(runningCount)} running)`
      : formatNumber(window.runs.length);

  return (
    <div>
      <StatSection title="Window">
        <div className="section-body">
          <table className="kv">
            <tbody>
              <tr>
                <th>Status</th>
                <td>
                  <StatusPill {...windowStatusPresentation(window.status)} />
                </td>
              </tr>
              <tr>
                <th>Period</th>
                <td>
                  {formatDate(window.start_time)}
                  {"\u2009\u2192\u2009"}
                  {formatDate(window.end_time)}
                </td>
              </tr>
              <tr>
                <th>Finalization</th>
                <td>
                  {window.finalized_at
                    ? `Frozen ${formatDateTime(window.finalized_at)}`
                    : `Deadline ${formatDateTime(window.finalization_deadline)}`}
                </td>
              </tr>
              <tr>
                <th>Time zone</th>
                <td>{window.business_timezone}</td>
              </tr>
              <tr>
                <th>Assignment</th>
                <td>{assignmentMethodLabel(window.assignment_method)}</td>
              </tr>
              <tr>
                <th>Eligible (N)</th>
                <td>{formatNumber(window.eligible_count)}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div className="section-body flush">
          <MetricStrip>
            <Metric label="Eligible (N)" value={formatNumber(window.eligible_count)} />
            <Metric label="Campaign" value={formatNumber(window.audience.campaign)} />
            <Metric label="Control" value={formatNumber(window.audience.control)} />
            <Metric label="Total" value={formatNumber(window.audience.total)} />
          </MetricStrip>
          <MetricStrip>
            <Metric label="Campaign / Control" value={splitText} />
            <Metric label="Runs" value={runsMetricText} />
          </MetricStrip>
        </div>
      </StatSection>

      <StatSection title="Runs">
        <div className="section-body flush">
          <table className="table">
            <thead>
              <tr>
                <th>Run</th>
                <th>Status</th>
                <th>Started</th>
                <th>Ended</th>
                <th>Note</th>
                <th>Segments</th>
                <th>Frozen snapshot</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {window.runs.map((run) => {
                const status = runStatusPresentation(run.status);
                return (
                  <tr key={run.id}>
                    <td className="small">Run #{run.id}</td>
                    <td>
                      <StatusPill {...runStatusPresentation(run.status)} />
                    </td>
                    <td className="small muted">{formatDateTime(run.started_at)}</td>
                    <td className="small muted">{formatDateTime(run.ended_at)}</td>
                    <td className="small muted">{run.note ?? "\u2014"}</td>
                    <td className="small">
                      {run.selected_segments.map((id) => segmentLabel(id, catalog)).join(", ")}
                    </td>
                    <td className="small">
                      <SnapshotCell capturedAt={run.snapshot.captured_at} files={run.snapshot.files} />
                    </td>
                    <td className="small">
                      {run.status === "running" ? (
                        <span className="muted">Running</span>
                      ) : (
                        <span className="muted">Completed</span>
                      )}
                    </td>
                  </tr>
                );
              })}
              {window.runs.length === 0 ? (
                <tr>
                  <td colSpan={8} className="small muted">
                    No runs started for this window yet.
                  </td>
                </tr>
) : null}
            </tbody>
          </table>
        </div>
        </StatSection>
      </div>
    );
  }

function AudienceTab({ window }: { window: WindowDetail }) {
  return (
    <div>
      <SplitSection split={window.split} />
    </div>
  );
}

function SourceDataTab({ windowId, status }: { windowId: number; status: string }) {
  return <DataStateSection windowId={windowId} status={status} />;
}

function StatisticsTab({ report }: { report: WindowReport }) {
  return (
    <div>
      <CampaignAttribution group={report.groups.campaign} />
      <GroupMetrics group={report.groups.campaign} title="Campaign group" />
      <GroupMetrics group={report.groups.control} title="Control group" />
      <StatSection title="Games">
        {(["campaign", "control"] as const).map((kind) => {
          const games = report.games[kind];
          return (
            <div className="section-body flush" key={`games-${kind}`}>
              <h3 className="group-title">
                {kind} group
              </h3>
              <table className="table">
                <thead>
                  <tr>
                    <th>Game</th>
                    <th>Plays</th>
                    <th>Customers</th>
                    <th>Amount</th>
                    <th>Avg amount</th>
                  </tr>
                </thead>
                <tbody>
                  {games.map((g) => (
                    <tr key={g.game_name}>
                      <td className="small">{g.game_name}</td>
                      <td className="small num">{formatNumber(g.plays)}</td>
                      <td className="small num">{formatNumber(g.customers)}</td>
                      <td className="small num">{formatMoney(g.amount)}</td>
                      <td className="small num">{formatMoney(g.avg_amount)}</td>
                    </tr>
                  ))}
                  {games.length === 0 ? (
                    <tr>
                      <td colSpan={5} className="small muted">
                        No plays in this group.
                      </td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </div>
          );
        })}
      </StatSection>
      <RunContributionsTable report={report} />
    </div>
  );
}

function ReportTab({ report }: { report: WindowReport }) {
  return <ReportSection report={report} />;
}

function ConfigurationTab({ window, onChanged, flash }: { window: WindowDetail; onChanged: () => void; flash: (f: Flash) => void }) {
  return (
    <div>
      <ConfigSection window={window} />
      <SplitSection split={window.split} />
      <ControlConfiguration window={window} onChanged={onChanged} flash={flash} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function WindowWorkspacePage() {
  const params = useParams<{ id: string }>();
  const windowId = Number(params.id);
  const now = useNow(60000);
  const [flash, setFlash] = useState<Flash | null>(null);
  const [activeTab, setActiveTab] = useState("overview");

  const detail = useQuery<WindowDetail>(() => apiGet(`/windows/${windowId}`), [windowId], 30000);
  const segments = useQuery<SegmentsResponse>(() => apiGet("/segments"), []);
  const report = useQuery<WindowReport>(() => apiGet(`/windows/${windowId}/report`), [windowId], 60000);

  function reloadAll() {
    detail.reload();
    report.reload();
  }

  if (detail.loading && !detail.data) {
    return (
      <div className="page">
        <div className="page-head">
          <h1>Campaign window</h1>
          <Link className="btn btn-secondary" href="/windows">
            Back to windows
          </Link>
        </div>
        <div className="section">
          <Loading text={"Loading campaign window..."} />
        </div>
      </div>
    );
  }

  if (detail.error || !detail.data) {
    return (
      <div className="page">
        <div className="page-head">
          <h1>Campaign window</h1>
          <Link className="btn btn-secondary" href="/windows">
            Back to windows
          </Link>
        </div>
        <div className="section">
          <div className="section-body">
            <ErrorBlock message="We couldn't load this campaign window." onRetry={detail.reload} />
          </div>
        </div>
      </div>
    );
  }

  const window = detail.data;
  const status = windowStatusPresentation(window.status);
  const reportState = reportStatePresentation(window.report_state);
  const catalog = segments.data?.items;
  const reportLoaded = report.data !== null;

  const tabs = [
    { id: "overview", label: "Overview" },
    { id: "runs", label: "Runs" },
    { id: "audience", label: "Audience" },
    { id: "source-data", label: "Source Data" },
    { id: "statistics", label: "Statistics" },
    { id: "report", label: "Report" },
    { id: "configuration", label: "Configuration" },
  ];

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
            <h1>{windowDisplayName(window.name, window.id)}</h1>
            <StatusPill {...status} />
            <StatusPill {...reportState} />
          </div>
          <p className="hint" style={{ margin: "4px 0 0" }}>
            {formatDate(window.start_time)}
            {"\u2009\u2192\u2009"}
            {formatDate(window.end_time)}, {window.business_timezone}
          </p>
        </div>
        <div className="actions">
          <ExportEvaluation windowId={window.id} flash={setFlash} />
          <Link className="btn btn-secondary" href="/windows">
            Back to windows
          </Link>
          <button className="btn btn-secondary" onClick={detail.reload}>
            Refresh
          </button>
        </div>
      </div>

      {flash && (
        <Notice kind={flash.kind} dismissMs={7000}>
          {flash.text}
        </Notice>
      )}

      <StatusBanner
        status={window.status}
        finalization_deadline={window.finalization_deadline}
        finalized_at={window.finalized_at}
        now={now}
      />

      <div className="section">
        <LifecycleControls
          windowId={window.id}
          status={window.status}
          endTime={window.end_time}
          finalizationDeadline={window.finalization_deadline}
          finalizedAt={window.finalized_at}
          now={now}
          onChanged={reloadAll}
          flash={setFlash}
        />
      </div>

      <Tabs tabs={tabs} activeTab={activeTab} onChange={setActiveTab} />

      <TabPanel id="overview" active={activeTab === "overview"}>
        <OverviewTab window={window} catalog={catalog} now={now} />
      </TabPanel>

      <TabPanel id="runs" active={activeTab === "runs"}>
        <RunsSection window={window} catalog={catalog} onChanged={reloadAll} flash={setFlash} />
      </TabPanel>

      <TabPanel id="audience" active={activeTab === "audience"}>
        <AudienceTab window={window} />
      </TabPanel>

      <TabPanel id="source-data" active={activeTab === "source-data"}>
        <SourceDataTab windowId={window.id} status={window.status} />
      </TabPanel>

      <TabPanel id="statistics" active={activeTab === "statistics"}>
        {report.loading && !report.data ? (
          <div className="section">
            <div className="section-body">
              <Loading text={"Loading window report..."} />
            </div>
          </div>
        ) : report.error ? (
          <div className="section">
            <div className="section-body">
              <ErrorBlock message="We couldn't load this window's report." onRetry={report.reload} />
            </div>
          </div>
        ) : reportLoaded ? (
          <StatisticsTab report={report.data as WindowReport} />
        ) : null}
      </TabPanel>

      <TabPanel id="report" active={activeTab === "report"}>
        {report.loading && !report.data ? (
          <div className="section">
            <div className="section-body">
              <Loading text={"Loading window report..."} />
            </div>
          </div>
        ) : report.error ? (
          <div className="section">
            <div className="section-body">
              <ErrorBlock message="We couldn't load this window's report." onRetry={report.reload} />
            </div>
          </div>
        ) : reportLoaded ? (
          <ReportTab report={report.data as WindowReport} />
        ) : null}
      </TabPanel>

      <TabPanel id="configuration" active={activeTab === "configuration"}>
        <ConfigurationTab window={window} onChanged={reloadAll} flash={setFlash} />
      </TabPanel>
    </div>
  );
}