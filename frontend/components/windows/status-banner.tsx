// Window lifecycle status banner (active / grace / finalized). Presents the
// backend window state verbatim; the copy spells out the workflow contract for
// each state (finalization deadline for live/grace windows, immutability for
// frozen reports).

import Notice from "@/components/notice";
import { formatDateTime, formatRelativeTime } from "@/lib/format";

interface Props {
  status: string;
  finalization_deadline: string;
  finalized_at: string | null;
  now?: Date;
}

export default function StatusBanner({ status, finalization_deadline, finalized_at, now }: Props) {
  const clock = now ?? new Date();
  const deadline = formatRelativeTime(finalization_deadline, clock);

  if (status === "active") {
    return (
      <Notice kind="info" dismissMs={0}>
        Window is live. Evaluation runs can start and dispatch Campaign SMS. Finalization deadline{" "}
        <strong>{formatDateTime(finalization_deadline)}</strong> ({deadline}).
      </Notice>
    );
  }
  if (status === "ended") {
    return (
      <Notice kind="warn" dismissMs={0}>
        Window ended. Late data can still update the report until the finalization deadline{" "}
        <strong>{formatDateTime(finalization_deadline)}</strong> ({deadline}). No new runs can start.
      </Notice>
    );
  }
  return (
    <Notice kind="success" dismissMs={0}>
      Finalized on {formatDateTime(finalized_at)}. The report is permanently frozen; no new runs can
      start and late data no longer changes results.
    </Notice>
  );
}