import Link from "next/link";

export default function NotFound() {
  return (
    <div className="page">
      <h1>Page not found</h1>
      <p className="muted" style={{ marginTop: 8 }}>
        The page you are looking for does not exist.
      </p>
      <Link href="/" className="btn btn-secondary btn-sm" style={{ marginTop: 16 }}>
        Back to dashboard
      </Link>
    </div>
  );
}