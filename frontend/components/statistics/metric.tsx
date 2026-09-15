// Metric building blocks shared across the Statistics surface. They render the
// dashboard's .stat/.stat-strip/.stat-tier/... classes so every report section
// presents counts identically without repeating the markup.

import type { ReactNode } from "react";

export function Metric({
  label,
  value,
  sub,
  accent = false,
}: {
  label: string;
  value: string;
  sub?: string;
  accent?: boolean;
}) {
  return (
    <div className="stat">
      <div className="label">{label}</div>
      <div className={`value${accent ? " accent" : ""}`}>{value}</div>
      {sub && <div className="sub">{sub}</div>}
    </div>
  );
}

// Full-width strip used for the headline row of a section.
export function MetricStrip({ children }: { children: ReactNode }) {
  return <div className="stat-strip">{children}</div>;
}

// 3-up grid of metrics (main outcome counts).
export function MetricTier({ children }: { children: ReactNode }) {
  return <div className="stat-tier">{children}</div>;
}

// 4-up grid of metrics (secondary outcome detail).
export function MetricTierSm({ children }: { children: ReactNode }) {
  return <div className="stat-tier-sm">{children}</div>;
}