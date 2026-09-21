"use client";

// The authoritative Control percentage configuration surface for a running
// Window. The effective Control percentage is computed from the operator
// override (when present) or the recommended formula, and the configuration is
// locked the moment the window's first Campaign Run starts - even if that Run
// admits no eligible users. From then on the percentage is fixed for the entire
// window: growing N never changes it, later Runs reuse it, and the backend
// rejects further overrides (POST /windows/{id}/control-override -> 409 once a
// Run has started).
//
// This component reflects exactly that state:
//   * before the first Run and window active -> editable override input
//   * locked (a Run has started)             -> read-only value, fixed for the
//                                               entire window (no Save action)
//   * grace / finalized                      -> read-only in all cases
//
// It is the only Control percentage configuration surface for a live window;
// the New-window form (app/windows/new) is the creation-time surface.

import { useState } from "react";
import StatSection from "@/components/statistics/section";
import { apiPost } from "@/lib/api";
import { formatPercentage } from "@/lib/format";
import type { WindowDetail } from "@/lib/types";

type Flash = { kind: "success" | "error"; text: string };

interface Props {
  window: WindowDetail;
  onChanged: () => void;
  flash: (f: Flash) => void;
}

export default function ControlConfiguration({ window, onChanged, flash }: Props) {
  const [override, setOverride] = useState("");
  const [busy, setBusy] = useState(false);

  const inactive = window.status !== "active";
  const locked = inactive || window.control_locked;

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
    setBusy(true);
    try {
      await apiPost(`/windows/${window.id}/control-override`, { percentage: value });
      flash({ kind: "success", text: `Control override set to ${formatPercentage(value)}.` });
      setOverride("");
      onChanged();
    } catch (err) {
      flash({ kind: "error", text: err instanceof Error ? err.message : "Could not set the override." });
    } finally {
      setBusy(false);
    }
  }

  return (
    <StatSection title="Configure">
      <div className="section-body">
        {locked ? (
          window.control_percentage != null ? (
            <div className="field">
              <span className="field-label">Control percentage</span>
              <div className="field-static">{formatPercentage(window.control_percentage)}</div>
              <span className="field-hint">
                {`${window.control_override != null ? "Set by an operator override" : "Established by the recommended formula"} before the first Campaign Run. It is `}
                <strong>fixed for the entire window</strong>
                {inactive
                  ? " and cannot be changed during the grace period or after finalization."
                  : " and can no longer be changed."}
              </span>
            </div>
          ) : (
            <div
              style={{ padding: 12, background: "rgba(0,0,0,0.03)", borderRadius: 6, border: "1px solid rgba(0,0,0,0.06)" }}
            >
              <p className="muted" style={{ margin: 0, fontSize: 13 }}>
                <strong>Configuration locked</strong> — the Control percentage can no longer be
                changed for this window. No percentage was established before the configuration
                locked.
              </p>
            </div>
          )
        ) : (
          <div className="form-grid">
            <div className="field">
              <label htmlFor="control-override">Control override %</label>
              <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
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
                <button className="btn btn-primary" disabled={busy} onClick={saveOverride}>
                  {busy ? "Saving\u2026" : "Save"}
                </button>
              </div>
              <span className="field-hint">
                Overrides the recommended split (max 50%). Set it before the first Campaign Run
                starts — the effective percentage becomes fixed for the entire window. Existing
                assignments are never rewritten.
              </span>
            </div>
          </div>
        )}
        <p className="muted" style={{ margin: "12px 0 0", fontSize: 12.5 }}>
          The eligible count (N) is derived by the evaluation step from the window&apos;s rules and the
          available source data. It is not set manually.
        </p>
      </div>
    </StatSection>
  );
}