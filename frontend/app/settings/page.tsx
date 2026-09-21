"use client";

import { useEffect, useState } from "react";
import Notice from "@/components/notice";
import { ErrorBlock, Loading } from "@/components/state-ui";
import { apiGet, apiPost } from "@/lib/api";
import type { SettingRow, SettingsResponse } from "@/lib/types";
import { useQuery } from "@/lib/use-query";

const GROUPS: { label: string; hint: string; keys: string[] }[] = [
  {
    label: "Campaigns",
    hint: "Who is targeted and how often. Values here apply to future campaigns.",
    keys: [
      "INACTIVITY_HOURS",
      "COOLDOWN_HOURS",
      "MAX_MESSAGES",
      "WELCOME_EVAL_DELAY_HOURS",
      "WELCOME_MAX_MESSAGES",
      "WELCOME_POST_LIMIT_SUPPRESS",
    ],
  },
  {
    label: "Messaging",
    hint: "Sender and template details, plus how sends behave.",
    keys: ["TERMII_SENDER_ID", "WELCOME_MESSAGE", "INACTIVE_MESSAGE", "ENABLED_FEATURES", "MAX_CONCURRENCY", "SMS_TIMEOUT"],
  },
  {
    label: "Timing",
    hint: "When cycles may run. Changing these affects the next cycle.",
    keys: ["START_TIME", "END_TIME", "CYCLE_END_HOUR"],
  },
  {
    label: "Data collection",
    hint: "How numbers are pulled in.",
    keys: ["CSV_DOWNLOADER_ENABLED"],
  },
];

function Control({ setting, value, onChange }: { setting: SettingRow; value: string; onChange: (v: string) => void }) {
  switch (setting.input_type) {
    case "textarea":
      return <textarea value={value} onChange={(e) => onChange(e.target.value)} placeholder={setting.description} />;
    case "checkbox":
      return (
        <label className="field-inline field" style={{ margin: 0, minHeight: 38, justifyContent: "flex-start" }}>
          <input type="checkbox" checked={value === "true"} onChange={(e) => onChange(e.target.checked ? "true" : "false")} />
          <span style={{ fontSize: 13.5 }}>{value === "true" ? "Enabled" : "Disabled"}</span>
        </label>
      );
    case "time":
      return <input type="time" value={value} onChange={(e) => onChange(e.target.value)} />;
    case "number":
      return (
        <input
          type="number"
          value={value}
          step="any"
          onChange={(e) => onChange(e.target.value)}
        />
      );
    case "tags":
      return <input type="text" value={value} onChange={(e) => onChange(e.target.value)} placeholder="welcome, inactive" />;
    default:
      return <input type="text" value={value} onChange={(e) => onChange(e.target.value)} />;
  }
}

export default function SettingsPage() {
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [savingKey, setSavingKey] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ kind: "success" | "error"; text: string } | null>(null);

  const settings = useQuery<SettingsResponse>(() => apiGet("/settings"));

  useEffect(() => {
    if (settings.data) {
      setDraft((prev) => {
        const next: Record<string, string> = {};
        for (const item of settings.data!.items) {
          next[item.key] = prev[item.key] ?? item.value;
        }
        return next;
      });
    }
  }, [settings.data]);

  const byKey = new Map(settings.data?.items.map((s) => [s.key, s]) ?? []);

  async function handleSave(setting: SettingRow) {
    const value = draft[setting.key] ?? setting.value;
    setSavingKey(setting.key);
    setNotice(null);
    try {
      const result = await apiPost<{ message: string; setting: { key: string; value: string } }>("/settings", {
        key: setting.key,
        value,
      });
      setNotice({ kind: "success", text: result.message });
      setDraft((prev) => ({ ...prev, [setting.key]: result.setting.value }));
      settings.reload();
    } catch (err) {
      setNotice({ kind: "error", text: err instanceof Error ? err.message : "Could not save this setting." });
    } finally {
      setSavingKey(null);
    }
  }

  if (settings.loading && !settings.data) {
    return (
      <div className="page">
        <h1>Settings</h1>
        <div className="section">
          <Loading text={"Loading settings..."} />
        </div>
      </div>
    );
  }

  if (settings.error || !settings.data) {
    return (
      <div className="page">
        <h1>Settings</h1>
        <div className="section">
          <div className="section-body">
            <ErrorBlock message="We couldn't load settings." onRetry={settings.reload} />
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="page">
      <div className="page-head">
        <h1>Settings</h1>
      </div>

      {notice && (
        <Notice kind={notice.kind} dismissMs={6000}>
          {notice.text}
        </Notice>
      )}

      {GROUPS.map((group) => {
        const rows = group.keys.map((k) => byKey.get(k)).filter((s): s is SettingRow => Boolean(s));
        if (rows.length === 0) return null;
        return (
          <section className="section" key={group.label}>
            <div className="section-head">
              <div>
                <h2>{group.label}</h2>
                <p className="muted" style={{ margin: "2px 0 0", fontSize: 12.5 }}>
                  {group.hint}
                </p>
              </div>
            </div>
            <div className="section-body flush">
              {rows.map((setting) => {
                const value = draft[setting.key] ?? setting.value;
                const dirty = value !== setting.value;
                return (
                  <div className="setting-row" key={setting.key}>
                    <div className="setting-info">
                      <div className="setting-label">{setting.label}</div>
                      <div className="muted" style={{ fontSize: 12.5, marginTop: 2 }}>
                        {setting.description}
                      </div>
                    </div>
                    <div className="setting-control">
                      <div style={{ width: "min(340px, 100%)" }}>
                        <Control setting={setting} value={value} onChange={(v) => setDraft((prev) => ({ ...prev, [setting.key]: v }))} />
                      </div>
                    </div>
                    <div className="setting-save">
                      <button
                        className="btn btn-secondary btn-sm"
                        disabled={!dirty || savingKey === setting.key}
                        onClick={() => handleSave(setting)}
                      >
                        {savingKey === setting.key ? "Saving..." : "Save"}
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          </section>
        );
      })}
    </div>
  );
}