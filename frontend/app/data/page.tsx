"use client";

import { useRef, useState } from "react";
import Notice from "@/components/notice";
import StatusPill from "@/components/status-pill";
import { Empty, ErrorBlock, Loading } from "@/components/state-ui";
import { apiGet, apiPostForm } from "@/lib/api";
import { datasetLabel, fileStatusPresentation, formatDateTime, formatNumber } from "@/lib/format";
import type { CampaignWindowRow, FilesResponse, ReportOverview, SettingsResponse, UploadResponse } from "@/lib/types";
import { useQuery } from "@/lib/use-query";

function describeUpload(result: UploadResponse): string {
  const accepted = result.records.filter((r) => r.status === "succeeded");
  const rejected = result.records.length - accepted.length + result.errors.length;

  if (accepted.length === 1 && rejected === 0) {
    const rec = accepted[0];
    const rows = rec.row_count;
    return `Accepted dataset ${datasetLabel(rec.dataset)}${rows != null ? `${formatNumber(rows)} rows` : ""}. It becomes available to the next evaluation run.`;
  }

  const total = accepted.length + rejected;
  const parts = [`${formatNumber(accepted.length)} of ${formatNumber(total)} file${total === 1 ? "" : "s"} accepted`];
  if (rejected > 0) parts.push(`${formatNumber(rejected)} rejected`);
  return `${parts.join(", ")}. Accepted files become available to the next evaluation run.`;
}

export default function DataPage() {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [notice, setNotice] = useState<{ kind: "success" | "error"; text: string } | null>(null);

  const files = useQuery<FilesResponse>(() => apiGet("/files"), [], 20000);
  const settings = useQuery<SettingsResponse>(() => apiGet("/settings"), []);
  const overview = useQuery<ReportOverview>(() => apiGet("/report/overview"), [], 30000);
  const windows = useQuery<CampaignWindowRow[]>(() => apiGet("/windows"), [], 10000);

  const scraperEnabled = settings.data?.items.find((s) => s.key === "CSV_DOWNLOADER_ENABLED")?.value === "true";
  const featureLabels: Record<string, string> = { welcome: "Welcome SMS", inactive: "Inactivity SMS" };
  // Active features come from the backend's scoped report, not from parsing
  // the ENABLED_FEATURES setting, so the UI can never drift from the scope.
  const features = overview.data?.features.enabled_features ?? [];

  // Check if any window has a running run
  const hasActiveRun = windows.data?.some(
    (w: CampaignWindowRow) => w.status === "active" && w.running_runs.length > 0
  ) ?? false;

  async function handleUpload(picked: File[]) {
    if (picked.length === 0) return;
    setUploading(true);
    setNotice(null);
    try {
      const form = new FormData();
      for (const file of picked) form.append("file", file);
      const result = await apiPostForm<UploadResponse>("/files", form);
      setNotice({ kind: "success", text: describeUpload(result) });
      files.reload();
    } catch (err) {
      setNotice({ kind: "error", text: err instanceof Error ? err.message : "Upload failed. Are the files valid CSVs?" });
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  }

  function handlePickChange(e: React.ChangeEvent<HTMLInputElement>) {
    const picked = e.target.files;
    if (picked && picked.length > 0) void handleUpload(Array.from(picked));
  }

  const failedCount = files.data?.items.filter((f) => f.status === "failed").length ?? 0;

  return (
    <div className="page">
      <div className="page-head">
        <h1>Data</h1>
      </div>

      {notice && (
        <Notice kind={notice.kind} dismissMs={8000}>
          {notice.text}
        </Notice>
      )}

      <section className="section">
        <div className="section-head">
          <h2>Automatic collection</h2>
        </div>
        <div className="section-body flush" style={{ paddingBottom: 0 }}>
          <div className="stat-strip" style={{ border: "none", borderRadius: 0 }}>
            <div className="stat">
              <div className="label">Features in use</div>
              <div className="value" style={{ fontSize: 16, paddingTop: 4 }}>
                {features.length > 0 ? features.map((f) => featureLabels[f] ?? f).join(", ") : "None"}
              </div>
              <div className="sub">Sent automatically after sign-ins / inactivity</div>
            </div>
            <div className="stat">
              <div className="label">Scraper downloader</div>
              <div className="value" style={{ fontSize: 16, paddingTop: 4 }}>
                {scraperEnabled ? "On" : "Off"}
              </div>
              <div className="sub">CSVs downloaded from ALOT BI automatically</div>
            </div>
          </div>
        </div>
        <div className="section-body" style={{ paddingTop: 12 }}>
          <p className="muted" style={{ margin: 0, fontSize: 13 }}>
            Automatic collection pulls new numbers on its own schedule. Use the manual upload below to add numbers now.
          </p>
        </div>
      </section>

      <section className="section">
        <div className="section-head">
          <h2>Manual upload</h2>
        </div>
        <div className="section-body">
          {hasActiveRun && (
            <Notice kind="warn" dismissMs={0}>
              Uploads are temporarily disabled because a Campaign Run is active. The Run's target
              audience is frozen at start and must not be affected by new source data. Wait for the
              Run to stop or complete before uploading.
            </Notice>
          )}
          <div className="upload-area">
            <input
              ref={fileInputRef}
              type="file"
              accept=".csv,text/csv"
              multiple
              className="hidden-input"
              onChange={handlePickChange}
              disabled={hasActiveRun || uploading}
            />
            <button
              className="btn btn-primary"
              disabled={hasActiveRun || uploading}
              onClick={() => fileInputRef.current?.click()}
            >
              {uploading ? "Uploading..." : hasActiveRun ? "Upload blocked (Run active)" : "Upload CSVs"}
            </button>
          </div>
          <p className="muted" style={{ margin: "10px 0 0", fontSize: 13 }}>
            Select one or more CSVs. They must have the same columns as the automated downloads. Parseable files
            join the matching dataset and are available to the next evaluation run; runs already started stay bound
            to their own frozen snapshots.
          </p>
        </div>
      </section>

      <section className="section">
        <div className="section-head">
          <h2>Upload history</h2>
          <span className="muted" style={{ fontSize: 12.5 }}>
            {files.data ? `${formatNumber(files.data.items.length)} shown` : "0"}
            {failedCount > 0 ? `${failedCount} failed` : ""}
          </span>
        </div>
        <div className="section-body flush">
          {files.loading && !files.data ? (
            <Loading text={"Loading uploads..."} />
          ) : files.error ? (
            <div className="section-body">
              <ErrorBlock message="We couldn't load the upload history." onRetry={files.reload} />
            </div>
          ) : !files.data || files.data.items.length === 0 ? (
            <Empty text="Nothing uploaded yet." />
          ) : (
            <table className="table">
              <thead>
                <tr>
                  <th>File</th>
                  <th>Dataset</th>
                  <th>Rows</th>
                  <th>Uploaded</th>
                  <th>Ingested</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {files.data.items.map((f) => {
                  const status = fileStatusPresentation(f.status);
                  return (
                    <tr key={f.id}>
                      <td className="small">{f.original_filename}</td>
                      <td className="small">{datasetLabel(f.dataset)}</td>
                      <td className="small num">{formatNumber(f.row_count)}</td>
                      <td className="small">{formatDateTime(f.uploaded_at)}</td>
                      <td className="small muted">{formatDateTime(f.processed_at)}</td>
                      <td>
                        <StatusPill {...status} />
                        {f.parse_error && <div className="muted small" style={{ marginTop: 2 }}>{f.parse_error}</div>}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      </section>
    </div>
  );
}