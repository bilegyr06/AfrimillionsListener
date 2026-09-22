"use client";

// Window lifecycle controls (active -> End, grace -> Finalize). The backend
// owns every transition and its guards; this surface only calls the endpoints
// and reflects the resulting state. A window that was ended early stays in
// grace until its scheduled end_time, and finalization is only valid between
// end_time and the finalization_deadline - both are surfaced here so the
// operator always knows why a button is (or isn't) available.

import { useState } from "react";
import ConfirmDialog from "@/components/confirm-dialog";
import { apiPost } from "@/lib/api";
import { formatDateTime, formatRelativeTime } from "@/lib/format";

type Flash = { kind: "success" | "error"; text: string };

interface Props {
  windowId: number;
  status: string;
  endTime: string;
  finalizationDeadline: string;
  finalizedAt: string | null;
  now: Date;
  onChanged: () => void;
  flash: (f: Flash) => void;
}

export default function LifecycleControls({
  windowId,
  status,
  endTime,
  finalizationDeadline,
  finalizedAt,
  now,
  onChanged,
  flash,
}: Props) {
  const [pending, setPending] = useState<"end" | "finalize" | null>(null);
  const [busy, setBusy] = useState(false);

  const endDate = new Date(endTime);
  const deadlineDate = new Date(finalizationDeadline);
  const beforeEnd = now.getTime() < endDate.getTime();
  const afterDeadline = now.getTime() > deadlineDate.getTime();
  const graceOpen = !beforeEnd && !afterDeadline;

  async function conclude() {
    if (!pending) return;
    setBusy(true);
    try {
      await apiPost(`/windows/${windowId}/${pending === "end" ? "end" : "finalize"}`);
      flash({
        kind: "success",
        text:
          pending === "end"
            ? "Window ended and active runs were closed. The grace period is open until the deadline."
            : "Window finalized. The report is frozen and can no longer change.",
      });
      setPending(null);
      onChanged();
    } catch (err) {
      flash({ kind: "error", text: err instanceof Error ? err.message : "The action was rejected." });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="section-body" style={{ display: "flex", gap: 16, flexWrap: "wrap", alignItems: "center", justifyContent: "space-between" }}>
      {status === "active" && (
        <>
          <div style={{ flex: "1 1 260px" }}>
            <p className="hint" style={{ margin: 0 }}>
              End the window to stop all active runs and enter the grace period. Configuration and the
              audience stay fixed from that moment on.
            </p>
          </div>
          <button className="btn btn-danger" onClick={() => setPending("end")} disabled={busy}>
            End window
          </button>
        </>
      )}

      {status === "ended" && (
        <>
          <div style={{ flex: "1 1 260px" }}>
            <p className="hint" style={{ margin: 0 }}>
              {beforeEnd
                ? `Window ended early. It stays in grace until the scheduled end ${formatDateTime(
                    endTime,
                  )}; finalization opens at that point.`
                : afterDeadline
                  ? `The finalization deadline ${formatDateTime(finalizationDeadline)} has passed. `
                  : `Finalization deadline ${formatDateTime(finalizationDeadline)} (${formatRelativeTime(
                      finalizationDeadline,
                      now,
                    )}). `}
              {!beforeEnd && !afterDeadline
                ? "Finalize to freeze the report and make the window immutable."
                : "The backend rejects finalization outside the grace period."}
            </p>
          </div>
          <button
            className="btn btn-primary"
            onClick={() => setPending("finalize")}
            disabled={busy || !graceOpen}
          >
            Finalize report
          </button>
        </>
      )}

      {status === "finalized" && (
        <p className="hint" style={{ margin: 0 }}>
          This window was finalized on {finalizedAt ? formatDateTime(finalizedAt) : "an earlier date"} and is
          immutable.
        </p>
      )}

      <ConfirmDialog
        open={pending !== null}
        title={pending === "end" ? "End this window?" : "Finalize this window?"}
        message={
          pending === "end"
            ? `Ending the window stops every active run and opens the grace period (data can still update the report until ${formatDateTime(
                finalizationDeadline,
              )}). Configuration and the audience become fixed, and no new runs can start.`
            : `Finalizing permanently freezes the report and the window. No configuration, audience, or data changes are possible afterwards. This cannot be undone.`
        }
        confirmLabel={pending === "end" ? "End window" : "Finalize report"}
        danger
        busy={busy}
        onCancel={() => setPending(null)}
        onConfirm={conclude}
      />
    </div>
  );
}