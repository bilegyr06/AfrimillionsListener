"use client";

export function Loading({ text = "Loading..." }: { text?: string }) {
  return (
    <div className="loading" role="status" aria-live="polite">
      <span className="spinner" />
      {text}
    </div>
  );
}

export function Empty({ text = "Nothing here yet." }: { text?: string }) {
  return <div className="empty">{text}</div>;
}

export function ErrorBlock({
  message,
  onRetry,
}: {
  message: string;
  onRetry?: () => void;
}) {
  return (
    <div className="error-block">
      <p>{message}</p>
      {onRetry && (
        <button className="btn btn-secondary btn-sm" style={{ marginTop: 8 }} onClick={onRetry}>
          Try again
        </button>
      )}
    </div>
  );
}