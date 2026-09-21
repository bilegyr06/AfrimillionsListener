// Small fetch client for the backend. Pages call /api/... (rewritten to the
// backend by Next). Errors are normalised into readable messages so the UI
// never shows raw exceptions.

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function readError(res: Response): Promise<string> {
  try {
    const body = (await res.json()) as { message?: string; detail?: unknown };
    if (body && typeof body.message === "string" && body.message.trim()) {
      return body.message;
    }
    // FastAPI-style validation/state errors arrive as {"detail": "..."}.
    if (body && typeof body.detail === "string" && body.detail.trim()) {
      return body.detail;
    }
  } catch {
    // not JSON; fall through
  }
  if (res.status === 404) return "This resource could not be found.";
  if (res.status === 400) return "The request was rejected.";
  if (res.status >= 500) return "The service had a problem. Please try again.";
  return "Something went wrong.";
}

async function parse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    throw new ApiError(res.status, await readError(res));
  }
  return (await res.json()) as T;
}

function queryString(params?: Record<string, string | number | undefined>): string {
  if (!params) return "";
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") {
      search.set(key, String(value));
    }
  }
  const qs = search.toString();
  return qs ? `?${qs}` : "";
}

export async function apiGet<T>(path: string, params?: Record<string, string | number | undefined>): Promise<T> {
  const res = await fetch(`/api${path}${queryString(params)}`, {
    cache: "no-store",
  });
  return parse<T>(res);
}

export async function apiPost<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method: "POST",
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  return parse<T>(res);
}

export async function apiPostForm<T>(path: string, formData: FormData): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method: "POST",
    body: formData,
  });
  return parse<T>(res);
}