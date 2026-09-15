"use client";

import { useCallback, useEffect, useRef, useState } from "react";

interface QueryState<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  reload: () => void;
}

// Fetch a resource on mount (and when `deps` change). Optionally auto-refresh
// every `pollMs` so the dashboard/SMS views stay current while open.
export function useQuery<T>(
  loader: () => Promise<T>,
  deps: unknown[] = [],
  pollMs?: number,
): QueryState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const loaderRef = useRef(loader);
  loaderRef.current = loader;
  const depsKey = JSON.stringify(deps);

  const reload = useCallback(async () => {
    try {
      setError(null);
      const result = await loaderRef.current();
      setData(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong.");
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [depsKey]);

  useEffect(() => {
    setLoading(true);
    void reload();
  }, [reload, depsKey]);

  useEffect(() => {
    if (!pollMs) return;
    const id = setInterval(() => {
      void loaderRef.current()
        .then(setData)
        .catch((err) => setError(err instanceof Error ? err.message : "Something went wrong."));
    }, pollMs);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pollMs, depsKey]);

  return { data, error, loading, reload };
}