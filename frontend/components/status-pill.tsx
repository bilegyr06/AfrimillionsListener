import type { Tone } from "@/lib/format";

const TONE_CLASS: Record<Tone, string> = {
  ok: "st-ok",
  bad: "st-bad",
  warn: "st-warn",
  neutral: "st-neutral",
  accent: "st-accent",
};

export default function StatusPill({
  label,
  tone = "neutral",
}: {
  label: string;
  tone: Tone;
}) {
  return <span className={`status ${TONE_CLASS[tone]}`}>{label}</span>;
}