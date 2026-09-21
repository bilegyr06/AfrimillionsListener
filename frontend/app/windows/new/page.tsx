"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import Notice from "@/components/notice";
import { apiPost } from "@/lib/api";
import { assignmentMethodLabel } from "@/lib/format";
import type { CreateWindowResponse } from "@/lib/types";

export default function NewWindowPage() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [override, setOverride] = useState<string>("");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [deadline, setDeadline] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit() {
    setBusy(true);
    setError(null);
    const parsedOverride = override.trim() === "" ? null : Number(override);
    if (parsedOverride !== null && (Number.isNaN(parsedOverride) || parsedOverride <= 0 || parsedOverride > 50)) {
      setError("Control override must be a percentage between 0 and 50.");
      setBusy(false);
      return;
    }
    try {
      const created = await apiPost<CreateWindowResponse>("/windows", {
        name: name.trim() === "" ? null : name.trim(),
        assignment_method: "deterministic",
        control_override: parsedOverride,
        start_time: start || null,
        end_time: end || null,
        finalization_deadline: deadline || null,
      });
      router.push(`/windows/${created.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create the window.");
      setBusy(false);
    }
  }

  return (
    <div className="page">
      <div className="page-head">
        <h1>New campaign window</h1>
        <Link className="btn btn-secondary" href="/windows">
          Back to windows
        </Link>
      </div>

      {error && (
        <Notice kind="error" dismissMs={0}>
          {error}
        </Notice>
      )}

      <section className="section">
        <div className="section-head">
          <h2>Window</h2>
        </div>
        <div className="section-body">
          <div className="form-grid">
            <label className="field">
              <span className="field-label">Name</span>
              <input
                className="input"
                value={name}
                placeholder="Week 39 window"
                onChange={(e) => setName(e.target.value)}
              />
            </label>

            <label className="field">
              <span className="field-label">Starts</span>
              <input
                className="input"
                type="datetime-local"
                value={start}
                onChange={(e) => setStart(e.target.value)}
              />
              <span className="field-hint">Leave empty for the default campaign week.</span>
            </label>

            <label className="field">
              <span className="field-label">Ends</span>
              <input
                className="input"
                type="datetime-local"
                value={end}
                onChange={(e) => setEnd(e.target.value)}
              />
              <span className="field-hint">Leave empty for the default (Saturday end).</span>
            </label>

            <label className="field">
              <span className="field-label">Finalization deadline</span>
              <input
                className="input"
                type="datetime-local"
                value={deadline}
                onChange={(e) => setDeadline(e.target.value)}
              />
              <span className="field-hint">Leave empty for the default (Sunday 14:00).</span>
            </label>
          </div>
        </div>
      </section>

      <section className="section">
        <div className="section-head">
          <h2>Assignment</h2>
        </div>
        <div className="section-body">
          <div className="form-grid">
            <label className="field">
              <span className="field-label">Assignment mode</span>
              <div className="field-static">{assignmentMethodLabel("deterministic")}</div>
              <span className="field-hint">
                Deterministic: each eligible user is assigned once for the whole window by a stable
                rule. Randomized assignment is not available.
              </span>
            </label>

            <label className="field">
              <span className="field-label">Control override % (optional)</span>
              <input
                className="input"
                type="number"
                min={0}
                max={50}
                step="0.1"
                value={override}
                placeholder="e.g. 15"
                onChange={(e) => setOverride(e.target.value)}
              />
              <span className="field-hint">
                Leave empty to use the recommended split from the backend formula. The Control
                percentage is fixed for the whole window once it is established, so choose it
                deliberately at creation.
              </span>
            </label>
          </div>
        </div>
      </section>

      <div className="page-actions">
        <button className="btn btn-primary" disabled={busy} onClick={handleSubmit}>
          {busy ? "Creating..." : "Create window"}
        </button>
      </div>
    </div>
  );
}