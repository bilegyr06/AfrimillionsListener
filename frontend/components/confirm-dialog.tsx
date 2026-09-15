"use client";

import { useEffect, useRef } from "react";

interface Props {
  open: boolean;
  title: string;
  message: string;
  confirmLabel?: string;
  danger?: boolean;
  busy?: boolean;
  children?: React.ReactNode;
  onCancel: () => void;
  onConfirm: () => void;
}

export default function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel = "Confirm",
  danger = false,
  busy = false,
  children,
  onCancel,
  onConfirm,
}: Props) {
  const dialogRef = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const el = dialogRef.current;
    if (!el) return;
    if (open) {
      if (!el.open) el.showModal();
    } else {
      if (el.open) el.close();
    }
  }, [open]);

  return (
    <dialog
      ref={dialogRef}
      className="dialog"
      onCancel={onCancel}
      onClick={(e) => {
        if ((e.target as HTMLElement).tagName === "DIALOG") onCancel();
      }}
    >
      <div className="dialog-head">
        <h2>{title}</h2>
      </div>
      <div className="dialog-body">
        <p>{message}</p>
        {children}
      </div>
      <div className="dialog-foot">
        <button className="btn btn-secondary btn-sm" disabled={busy} onClick={onCancel}>
          Cancel
        </button>
        <button
          className={`btn btn-sm ${danger ? "btn-danger" : "btn-primary"}`}
          disabled={busy}
          onClick={onConfirm}
        >
          {busy ? "Working\u2026" : confirmLabel}
        </button>
      </div>
    </dialog>
  );
}