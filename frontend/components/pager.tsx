"use client";

interface Props {
  page: number;
  pages: number;
  onChange: (page: number) => void;
}

export default function Pager({ page, pages, onChange }: Props) {
  if (pages <= 1) return null;
  return (
    <div className="pager">
      <button
        className="btn btn-secondary btn-sm"
        disabled={page <= 1}
        onClick={() => onChange(page - 1)}
      >
        Prev
      </button>
      <span className="muted">
        Page {page} of {pages}
      </span>
      <button
        className="btn btn-secondary btn-sm"
        disabled={page >= pages}
        onClick={() => onChange(page + 1)}
      >
        Next
      </button>
    </div>
  );
}