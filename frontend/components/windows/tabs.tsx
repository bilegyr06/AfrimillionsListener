"use client";

import type { ReactNode } from "react";

interface TabItem {
  id: string;
  label: string;
  icon?: ReactNode;
  disabled?: boolean;
}

interface TabsProps {
  tabs: TabItem[];
  activeTab: string;
  onChange: (tabId: string) => void;
  className?: string;
}

export function Tabs({ tabs, activeTab, onChange, className = "" }: TabsProps) {
  return (
    <div className={`tabs ${className}`} role="tablist">
      {tabs.map((tab) => (
        <button
          key={tab.id}
          role="tab"
          aria-selected={activeTab === tab.id}
          aria-controls={`panel-${tab.id}`}
          id={`tab-${tab.id}`}
          className={`tab${activeTab === tab.id ? " active" : ""}${tab.disabled ? " disabled" : ""}`}
          disabled={tab.disabled}
          onClick={() => !tab.disabled && onChange(tab.id)}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}

interface TabPanelProps {
  id: string;
  active: boolean;
  children: ReactNode;
  className?: string;
}

export function TabPanel({ id, active, children, className = "" }: TabPanelProps) {
  if (!active) return null;
  return (
    <div
      role="tabpanel"
      id={`panel-${id}`}
      aria-labelledby={`tab-${id}`}
      className={`tab-panel${className ? ` ${className}` : ""}`}
    >
      {children}
    </div>
  );
}