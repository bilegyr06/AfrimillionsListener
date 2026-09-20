"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import Notice from "@/components/notice";
import StatusPill from "@/components/status-pill";
import { Metric, MetricStrip, MetricTier } from "@/components/statistics/metric";
import StatSection from "@/components/statistics/section";
import CampaignAttribution from "@/components/windows/campaign-attribution";
import SnapshotCell from "@/components/windows/snapshot-cell";
import SplitSection from "@/components/windows/split-section";
import StatusBanner from "@/components/windows/status-banner";
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
  formatPercentage,
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

function ConfigSection({ window, catalog }: { window: WindowDetail; catalog?: SegmentsResponse["items"] }) {
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
              <th>Segments</th>
              <td>{window.selected_segments.map((id) => segmentLabel(id, catalog)).join(", ")}</td>
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
// Operator forms (eligible count + control override)
// ---------------------------------------------------------------------------

function OperatorForms({
  window,
  onChanged,
  flash,
}: {
  window: WindowDetail;
  onChanged: () => void;
  flash: (f: Flash) => void;
}) {
  const [n, setN] = useState("");
  const [override, setOverride] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const locked = window.status === "finalized";

  async function saveEligible() {
    const value = Number(n);
    if (!Number.isInteger(value) || value <= 0) {
      flash({ kind: "error", text: "Eligible count must be a positive whole number." });
      return;
    }
    setBusy("n");
    try {
      await apiPost(`/windows/${window.id}/eligible-count`, { eligible_count: value });
      flash({ kind: "success", text: "Eligible count saved. The recommended Control % is recalculated." });
      setN("");
      onChanged();
    } catch (err) {
      flash({ kind: "error", text: err instanceof Error ? err.message : "Could not save the eligible count." });
    } finally {
      setBusy(null);
    }
  }

  async function saveOverride() {
    if (override.trim() === "") {
      flash({ kind: "error", text: "Enter a Control override percentage." });
      return;
    }
    const value = Number(override);
    if (Number.isNaN(value) || value <= 0 || value > 50) {
      flash({ kind: "error", text: "Control override must be a percentage between 0 and 50." });
      return;
    }
    setBusy("override");
    try {
      await apiPost(`/windows/${window.id}/control-override`, { percentage: value });
      flash({ kind: "success", text: `Control override set to ${formatPercentage(value)}.` });
      setOverride("");
      onChanged();
    } catch (err) {
      flash({ kind: "error", text: err instanceof Error ? err.message : "Could not set the override." });
    } finally {
      setBusy(null);
    }
  }

  return (
    <StatSection title="Configure">
      <div className="section-body">
        {locked ? (
          <p className="muted" style={{ margin: 0, fontSize: 13 }}>
            This window is finalized and immutable — configuration can no longer be changed.
          </p>
        ) : (
          <div className="form-grid">
            <div className="field">
              <label htmlFor="eligible-count">Eligible count (N)</label>
              <div style={{ display: "flex", gap: 8 }}>
                <input
                  id="eligible-count"
                  className="input"
                  type="number"
                  min={1}
                  step={1}
                  value={n}
                  placeholder="e.g. 20000"
                  onChange={(e) => setN(e.target.value)}
                  style={{ flex: 1 }}
                />
                <button className="btn btn-secondary" disabled={busy !== null} onClick={saveEligible}>
                  {busy === "n" ? "Saving\u2026" : "Save"}
                </button>
              </div>
              <span className="field-hint">
                Sum of eligible users across the selected segments. Used to compute the recommended
                Control %.
              </span>
            </div>

            <div className="field">
              <label htmlFor="control-override">Control override %</label>
              <div style={{ display: "flex", gap: 8 }}>
                <input
                  id="control-override"
                  className="input"
                  type="number"
                  min={0}
                  max={50}
                  step="0.1"
                  value={override}
                  placeholder="e.g. 15"
                  onChange={(e) => setOverride(e.target.value)}
                  style={{ flex: 1 }}
                />
                <button className="btn btn-secondary" disabled={busy !== null} onClick={saveOverride}>
                  {busy === "override" ? "Saving\u2026" : "Save"}
                </button>
              </div>
              <span className="field-hint">
                Overrides the recommended split (max 50%). Existing assignments are never rewritten.
              </span>
            </div>
          </div>
        )}
      </div>
    </StatSection>
  );
}

// ---------------------------------------------------------------------------
// Runs (operator surface)
// ---------------------------------------------------------------------------

function RunsSection({
  window,
  onChanged,
  flash,
}: {
  window: WindowDetail;
  onChanged: () => void;
  flash: (f: Flash) => void;
}) {
  const [note, setNote] = useState("");
  const [starting, setStarting] = useState(false);
  const [busyRun, setBusyRun] = useState<number | null>(null);
  const [result, setResult] = useState<{
    runId: number;
    kind: "evaluate" | "dispatch";
    text: string;
  } | null>(null);

  const needsEligibility = window.eligible_count === null || window.audience.total === 0;

  async function startRun() {
    setStarting(true);
    try {
      await apiPost(`/windows/${window.id}/runs`, { note: note.trim() === "" ? null : note.trim() });
      flash({ kind: "success", text: "Run started with a frozen snapshot of the current data." });
      setNote("");
      onChanged();
    } catch (err) {
      flash({ kind: "error", text: err instanceof Error ? err.message : "Could not start a run." });
    } finally {
      setStarting(false);
    }
  }

  async function act(runId: number, action: "evaluate" | "dispatch" | "stop" | "complete") {
    setBusyRun(runId);
    setResult(null);
    try {
      if (action === "evaluate") {
        const res = await apiPost<EvaluateRunResponse>(`/runs/${runId}/evaluate`);
        setResult({ runId, kind: "evaluate", text: evaluateResultText(res) });
      } else if (action === "dispatch") {
        const res = await apiPost<DispatchResult>(`/runs/${runId}/dispatch`);
        setResult({ runId, kind: "dispatch", text: dispatchResultText(res) });
      } else {
        await apiPost(`/runs/${runId}/${action}`);
        flash({ kind: "success", text: `Run \u00b7 ${action} accepted.` });
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
              {starting ? "Starting\u2026" : "Start run"}
            </button>
          </div>
          {needsEligibility && (
            <p className="muted" style={{ margin: "10px 0 0", fontSize: 12.5 }}>
              No audience yet. After starting, use <strong>Evaluate</strong> to build the audience from
              the run&apos;s frozen snapshot.
            </p>
          )}
        </div>
      )}

      {result && (
        <div className="section-body">
          <Notice kind="success" dismissMs={12000}>
            <strong>Run #{result.runId}</strong> \u00b7 {result.text}
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
                    <SnapshotCell capturedAt={run.snapshot.captured_at} files={run.snapshot.files} />
                  </td>
                  <td className="small">
                    {running ? (
                      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                        <button
                          className="btn btn-secondary btn-sm"
                          disabled={busyRun !== null}
                          onClick={() => act(run.id, "evaluate")}
                        >
                          {busyRun === run.id ? "Evaluating\u2026" : "Evaluate"}
                        </button>
                        <button
                          className="btn btn-secondary btn-sm"
                          disabled={busyRun !== null}
                          onClick={() => act(run.id, "dispatch")}
                        >
                          {busyRun === run.id ? "Dispatching\u2026" : "Dispatch SMS"}
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
                <td colSpan={7} className="small muted">
                  No runs started for this window yet.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
        <div className="section-body">
          <p className="muted" style={{ margin: 0, fontSize: 12.5 }}>
            This run uses a snapshot of the data available at start. Later uploads do not change this
            run&apos;s target.
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
          <Loading text="Loading data state\u2026" />
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
            <p className="muted" style={{ margin: 0, fontSize: 12.5 }}>
              Captured {formatDateTime(state.data.captured_at)}. Files shown here are available to a
              new run; existing runs stay bound to the snapshot they captured at start.
            </p>
          </div>
        </div>
      ) : null}
      {status === "finalized" && (
        <div className="section-body">
          <p className="muted" style={{ margin: 0, fontSize: 12.5 }}>
            Window finalized \u2014 the report is frozen and ignores everything uploaded after
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
              <h3 style={{ margin: "0 20px 8px", fontSize: 13, textTransform: "capitalize" }}>
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
// Page
// ---------------------------------------------------------------------------

export default function WindowWorkspacePage() {
  const params = useParams<{ id: string }>();
  const windowId = Number(params.id);
  const now = useNow(60000);
  const [flash, setFlash] = useState<Flash | null>(null);

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
          <Loading text="Loading campaign window\u2026" />
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

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
            <h1>{windowDisplayName(window.name, window.id)}</h1>
            <StatusPill {...status} />
            <StatusPill {...reportState} />
          </div>
          <p className="muted" style={{ margin: "4px 0 0", fontSize: 13 }}>
            {formatDate(window.start_time)}
            {"\u2009\u2192\u2009"}
            {formatDate(window.end_time)} \u00b7 {window.business_timezone}
          </p>
        </div>
        <div className="actions">
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

      <ConfigSection window={window} catalog={catalog} />
      <SplitSection split={window.split} />
      <OperatorForms window={window} onChanged={reloadAll} flash={setFlash} />
      <RunsSection window={window} onChanged={reloadAll} flash={setFlash} />
      <DataStateSection windowId={window.id} status={window.status} />

      {report.loading && !report.data ? (
        <div className="section">
          <div className="section-body">
            <Loading text="Loading window report\u2026" />
          </div>
        </div>
      ) : report.error ? (
        <div className="section">
          <div className="section-body">
            <ErrorBlock message="We couldn't load this window's report." onRetry={report.reload} />
          </div>
        </div>
      ) : reportLoaded ? (
        <ReportSection report={report.data as WindowReport} />
      ) : null}
    </div>
  );
}