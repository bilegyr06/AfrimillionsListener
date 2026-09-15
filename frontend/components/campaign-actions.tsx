"use client";

import { useState } from "react";
import ConfirmDialog from "@/components/confirm-dialog";
import { apiPost } from "@/lib/api";
import type { CampaignLifecycleResult, CloseCampaignResult } from "@/lib/types";

export function useCampaignActions() {
  const [busy, setBusy] = useState(false);

  async function start(name?: string) {
    setBusy(true);
    try {
      return await apiPost<CampaignLifecycleResult>(
        "/campaign/start",
        name?.trim() ? { name: name.trim() } : {},
      );
    } finally {
      setBusy(false);
    }
  }

  async function close() {
    setBusy(true);
    try {
      return await apiPost<CloseCampaignResult>("/campaign/close");
    } finally {
      setBusy(false);
    }
  }

  return { busy, start, close };
}

export function StartCampaignDialog({
  open,
  busy,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  busy?: boolean;
  onConfirm: (name?: string) => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState("");
  return (
    <ConfirmDialog
      open={open}
      title="Start a new campaign"
      message="A new Welcome campaign opens now. If another campaign is still active, it is closed and finalised first. Sending follows the configured schedule."
      confirmLabel="Start campaign"
      busy={busy}
      onCancel={onCancel}
      onConfirm={() => onConfirm(name)}
    >
      <div className="field" style={{ marginTop: 12 }}>
        <label htmlFor="campaign-name">Campaign name (optional)</label>
        <input
          id="campaign-name"
          type="text"
          value={name}
          maxLength={200}
          placeholder="e.g. March welcome week"
          onChange={(e) => setName(e.target.value)}
        />
      </div>
    </ConfirmDialog>
  );
}

export function CloseCampaignDialog({
  open,
  busy,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <ConfirmDialog
      open={open}
      title="Close the active campaign?"
      danger
      message="Closing stops new sends and finalises this campaign's conversions. Campaign data and reports remain available."
      confirmLabel="Close campaign"
      busy={busy}
      onCancel={onCancel}
      onConfirm={onConfirm}
    />
  );
}