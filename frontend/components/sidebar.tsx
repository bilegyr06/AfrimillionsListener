"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { apiGet } from "@/lib/api";
import type { HealthResponse } from "@/lib/types";

const NAV = [
  { href: "/", label: "Dashboard", icon: "dashboard" },
  { href: "/campaigns", label: "Campaigns", icon: "campaigns" },
  { href: "/sms", label: "SMS Activity", icon: "sms" },
  { href: "/data", label: "Data", icon: "data" },
  { href: "/settings", label: "Settings", icon: "settings" },
] as const;

function NavIcon({ name }: { name: string }) {
  switch (name) {
    case "dashboard":
      return (
        <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <rect x="2" y="2" width="5" height="5" rx="1" />
          <rect x="9" y="2" width="5" height="5" rx="1" />
          <rect x="2" y="9" width="5" height="5" rx="1" />
          <rect x="9" y="9" width="5" height="5" rx="1" />
        </svg>
      );
    case "campaigns":
      return (
        <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <path d="M3 2v12" />
          <path d="M3 2h9a1 1 0 011 1v4a1 1 0 01-1 1H7" />
        </svg>
      );
    case "sms":
      return (
        <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <path d="M13 9.5a6 6 0 01-10.392 3.487L2 14l1.013-2.608A6 6 0 0113 4.5z" />
        </svg>
      );
    case "data":
      return (
        <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <path d="M8 11V3m0 0L5 6m3-3l3 3" />
          <path d="M3 11v2a1 1 0 001 1h8a1 1 0 001-1v-2" />
        </svg>
      );
    case "settings":
      return (
        <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <path d="M6.5 2h3l.3 1.8a5 5 0 011.6 1l1.6-.6 1.5 2.6-1.2 1.3a5 5 0 010 1.4l1.2 1.3-1.5 2.6-1.6-.6a5 5 0 01-1.6 1L9.5 15h-3l-.3-1.8a5 5 0 01-1.6-1l-1.6.6-1.5-2.6 1.2-1.3a5 5 0 010-1.4l-1.2-1.3L3.3 2.7l1.6.6A5 5 0 016.5 2.3z" />
          <circle cx="8" cy="8.5" r="1.5" />
        </svg>
      );
    default:
      return null;
  }
}

export default function Sidebar() {
  const pathname = usePathname();
  const [serviceOk, setServiceOk] = useState<boolean | null>(null);

  useEffect(() => {
    let alive = true;
    apiGet<HealthResponse>("/health")
      .then(() => { if (alive) setServiceOk(true); })
      .catch(() => { if (alive) setServiceOk(false); });
    return () => { alive = false; };
  }, []);

  const isActive = (href: string) =>
    href === "/" ? pathname === "/" : pathname.startsWith(href);

  return (
    <aside className="sidebar" aria-label="Primary">
      <div className="brand">
        <span className="brand-name">Afrimillions</span>
        <span className="brand-sub">Listener console</span>
      </div>

      <nav className="nav">
        {NAV.map(({ href, label, icon }) => (
          <Link
            key={href}
            href={href}
            className={`nav-link${isActive(href) ? " active" : ""}`}
            aria-current={isActive(href) ? "page" : undefined}
          >
            <NavIcon name={icon} />
            {label}
          </Link>
        ))}
      </nav>

      <div className="sidebar-footer">
        <span className="status-row">
          <span className={`dot ${serviceOk === true ? "dot-ok" : serviceOk === false ? "dot-bad" : "dot-pending"}`} />
          {serviceOk === true && "Service online"}
          {serviceOk === false && "Service unreachable"}
          {serviceOk === null && "Checking\u2026"}
        </span>
      </div>
    </aside>
  );
}