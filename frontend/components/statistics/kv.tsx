// Key/value rows for textual report values (campaign window, timing detail,
// economics). Reuses the campaign page's .kv table styling.

export interface KvRow {
  label: string;
  value: string;
}

export function KvRows({ rows }: { rows: KvRow[] }) {
  if (rows.length === 0) return null;
  return (
    <table className="kv">
      <tbody>
        {rows.map((row) => (
          <tr key={row.label}>
            <th>{row.label}</th>
            <td>{row.value}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}