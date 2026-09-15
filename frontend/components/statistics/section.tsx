// Section wrapper reused across the Statistics surface so new report sections
// (game activity, player activity, economics) drop in without layout rework.

import type { ReactNode } from "react";

export default function StatSection({
  title,
  aside,
  children,
}: {
  title: string;
  aside?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="section">
      <div className="section-head">
        <h2>{title}</h2>
        {aside}
      </div>
      {children}
    </section>
  );
}