"use client";

import { useState } from "react";
import Notice from "@/components/notice";
import StatusPill from "@/components/status-pill";
import { Empty, ErrorBlock, Loading } from "@/components/state-ui";
import { apiGet, apiPostForm } from "@/lib/api";
import { fileStatusPresentation, formatDateTime, formatNumber } from "@/lib/format";
import type { FilesResponse, SettingsResponse, UploadResponse } from "@/lib/types";
import { useQuery } from "@/lib/use-query";

export default function DataPage() {
  const [file, setFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [notice, setNotice] = useState<{ kind: "success" | "error"; text: string } | null>(null);

  const files = useQuery<FilesResponse>(() => apiGet("/files"), [], 20000);
  const settings = useQuery<SettingsResponse>(() => apiGet("/settings"), []);

  const enabledFeatures = settings.data?.items.find((s) => s.key === "ENABLED_FEATURES")?.value ?? "";
  const scraperEnabled = settings.data?.items.find((s) => s.key === "CSV_DOWNLOADER_ENABLED")?.value === "true";
  const featureLabels: Record<string, string> = { welcome: "Welcome SMS", inactive: "Inactivity SMS" };
  const features = enabledFeatures
    .split(",")
    .map((f) => f.trim())
    .filter(Boolean);

  async function handleUpload() {
    if (!file) return;
    setUploading(true);
    setNotice(null);
    try {
      const form = new FormData();
      form.append("file", file);
      const result = await apiPostForm<UploadResponse>("/files", form);
      const labels: Record<string, string> = {
        invitations: "Invitations",
        sessions: "Sessions",
        unknown: "Unrecognised (not matching any dataset)",
      };
      const rows = result.record.row_count;
      setNotice({
        kind: "success",
        text: `${result.message} Dataset ${labels[result.record.dataset] ?? result.record.dataset}${rows != null ? ` \u00b7 ${formatNumber(rows)} rows` : ""}. It becomes visible to the next cycle.`,
      });
      setFile(null);
      files.reload();
    } catch (err) {
      setNotice({ kind: "error", text: err instanceof Error ? err.message : "Upload failed. Is the file a valid CSV?" });
    } finally {
      setUploading(false);
    }
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
          <div className="upload-area">
            <input
              type="file"
              accept=".csv,text/csv"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            />
            <button className="btn btn-primary" disabled={!file || uploading} onClick={handleUpload}>
              {uploading ? "Uploading\u2026" : "Upload CSV"}
            </button>
          </div>
          <p className="muted" style={{ margin: "10px 0 0", fontSize: 13 }}>
            CSV must have the same columns as the automated downloads. Parseable files join the matching dataset and go live on the next cycle.
          </p>
        </div>
      </section>

      <section className="section">
        <div className="section-head">
          <h2>Upload history</h2>
          <span className="muted" style={{ fontSize: 12.5 }}>
            {files.data ? `${formatNumber(files.data.items.length)} shown` : "\u00a0"}
            {failedCount > 0 ? ` \u00b7 ${failedCount} failed` : ""}
          </span>
        </div>
        <div className="section-body flush">
          {files.loading && !files.data ? (
            <Loading text="Loading uploads\u2026" />
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
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {files.data.items.map((f) => {
                  const status = fileStatusPresentation(f.status);
                  const datasetLabels: Record<string, string> = {
                    invitations: "Invitations",
                    sessions: "Sessions",
                    unknown: "Unrecognised",
                  };
                  return (
                    <tr key={f.id}>
                      <td className="small">{f.original_filename}</td>
                      <td className="small">{datasetLabels[f.dataset] ?? f.dataset}</td>
                      <td className="small num">{formatNumber(f.row_count)}</td>
                      <td className="small">{formatDateTime(f.uploaded_at)}</td>
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