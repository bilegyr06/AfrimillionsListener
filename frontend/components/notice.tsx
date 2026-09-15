"use client";

import { useEffect, useState } from "react";

interface Props {
  kind: "info" | "warn" | "success" | "error";
  children: React.ReactNode;
  dismissMs?: number;
}

export default function Notice({ kind, children, dismissMs }: Props) {
  const [visible, setVisible] = useState(true);
  useEffect(() => {
    if (dismissMs) {
      const id = setTimeout(() => setVisible(false), dismissMs);
      return () => clearTimeout(id);
    }
  }, [dismissMs]);
  if (!visible) return null;
  return (
    <div className={`notice notice-${kind}`} role="status" aria-live="polite">
      {children}
    </div>
  );
}