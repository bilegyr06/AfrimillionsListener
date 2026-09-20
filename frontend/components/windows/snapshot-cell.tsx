// A run's frozen source snapshot: when it was captured and which files the run
// is bound to. The note below spells out that later uploads never change an
// already-started run's target.

import { datasetLabel, formatDateTime, formatNumber } from "@/lib/format";
import type { SnapshotFile } from "@/lib/types";

interface Props {
  capturedAt: string | null;
  files: SnapshotFile[];
}

export default function SnapshotCell({ capturedAt, files }: Props) {
  if (!capturedAt) {
    return <span className="muted small">No snapshot</span>;
  }
  return (
    <details>
      <summary className="small">
        {files.length} files \u00b7 captured {formatDateTime(capturedAt)}
      </summary>
      {files.length > 0 ? (
        <table className="table" style={{ marginTop: 8 }}>
          <thead>
            <tr>
              <th>Dataset</th>
              <th>File</th>
              <th>Rows</th>
            </tr>
          </thead>
          <tbody>
            {files.map((file) => (
              <tr key={`${file.dataset}-${file.filename}`}>
                <td className="small">{datasetLabel(file.dataset)}</td>
                <td className="small">{file.filename}</td>
                <td className="small num">{formatNumber(file.row_count)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      <p className="muted" style={{ margin: "6px 0 0", fontSize: 12.5 }}>
        This run uses a snapshot of the data available at start. Later uploads do not change this
        run&apos;s target.
      </p>
    </details>
  );
}