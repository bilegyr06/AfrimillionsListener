"use client";

// Campaign Window evaluation export. The backend streams the .xlsx workbook
// (segment-by-segment Campaign/Control evaluation); the UI only triggers the
// download from the Content-Disposition filename and reflects the busy/error
// state. No report metrics are computed here.

import { useState } from "react";
import { apiDownload } from "@/lib/api";

interface Props {
  windowId: number;
  flash: (f: { kind: "success" | "error"; text: string }) => void;
}

export default function ExportEvaluation({ windowId, flash }: Props) {
  const [busy, setBusy] = useState(false);

  async function handleExport() {
    if (busy) return;
    setBusy(true);
    try {
      const { filename, blob } = await apiDownload(`/windows/${windowId}/export`);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filename;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      flash({ kind: "error", text: err instanceof Error ? err.message : "Could not export the evaluation." });
    } finally {
      setBusy(false);
    }
  }

  return (
    <button className="btn btn-secondary" onClick={handleExport} disabled={busy}>
      {busy ? "Exporting..." : "Export evaluation"}
    </button>
  );
}