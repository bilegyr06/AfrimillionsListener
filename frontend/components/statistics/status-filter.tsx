// Segmented status filter for the statistics overview. Filtering happens over
// the already-fetched summaries, so changing it never triggers a new request.

export type CampaignStatusFilter = "all" | "active" | "closed";

const OPTIONS: Array<{ value: CampaignStatusFilter; label: string }> = [
  { value: "all", label: "All" },
  { value: "active", label: "Active" },
  { value: "closed", label: "Closed" },
];

export function StatusFilter({
  value,
  onChange,
}: {
  value: CampaignStatusFilter;
  onChange: (value: CampaignStatusFilter) => void;
}) {
  return (
    <div className="filter-bar" role="group" aria-label="Filter campaigns by status">
      {OPTIONS.map((opt) => (
        <button
          key={opt.value}
          type="button"
          className={`btn btn-sm${value === opt.value ? " btn-primary" : " btn-ghost"}`}
          aria-pressed={value === opt.value}
          onClick={() => onChange(opt.value)}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}