"use client";

import { useEffect, useRef, useState } from "react";

interface Props {
  open: boolean;
  title: string;
  text: string;
  filename: string;
  onClose: () => void;
}

export default function SnapshotDialog({ open, title, text, filename, onClose }: Props) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    const el = dialogRef.current;
    if (!el) return;
    if (open) {
      setCopied(false);
      if (!el.open) el.showModal();
    } else {
      if (el.open) el.close();
    }
  }, [open]);

  useEffect(() => {
    if (!copied) return;
    const id = setTimeout(() => setCopied(false), 2000);
    return () => clearTimeout(id);
  }, [copied]);

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  }

  function handleDownload() {
    const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  return (
    <dialog
      ref={dialogRef}
      className="dialog dialog-wide"
      onCancel={onClose}
      onClick={(e) => {
        if ((e.target as HTMLElement).tagName === "DIALOG") onClose();
      }}
    >
      <div className="dialog-head">
        <h2>{title}</h2>
        <p className="muted" style={{ margin: "4px 0 0", fontSize: 13 }}>
          Copy or download for your reports.
        </p>
      </div>
      <div className="dialog-body">
        <pre className="snapshot-pre">{text}</pre>
      </div>
      <div className="dialog-foot">
        {copied && <span className="snapshot-copied">Copied to clipboard</span>}
        <button className="btn btn-secondary btn-sm" onClick={handleDownload}>
          Download .txt
        </button>
        <button className="btn btn-primary btn-sm" onClick={handleCopy}>
          {copied ? "Copied" : "Copy to clipboard"}
        </button>
        <button className="btn btn-ghost btn-sm" onClick={onClose}>
          Close
        </button>
      </div>
    </dialog>
  );
}