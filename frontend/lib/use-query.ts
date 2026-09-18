"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export interface QueryState<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  reload: () => void;
}

// Fetch a resource on mount (and when `deps` change). Optionally auto-refresh
// every `pollMs` so the dashboard/SMS views stay current while open.
//
// A monotonic request sequence guards every state commit: only the most recent
// load may write to state, so a slow response for stale deps (or a poll that
// overlapped a reload or an unmount) can never clobber newer data.
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
  const seqRef = useRef(0);

  const run = useCallback((id: number) => {
    return loaderRef.current().then(
      (result) => {
        if (id !== seqRef.current) return;
        setData(result);
        setError(null);
        setLoading(false);
      },
      (err) => {
        if (id !== seqRef.current) return;
        setError(err instanceof Error ? err.message : "Something went wrong.");
        setLoading(false);
      },
    );
  }, []);

  // Manual refresh. Error is cleared eagerly so the page can drop the error
  // state immediately; loading is left untouched so refetches don't flash a
  // spinner over data that is already on screen.
  const reload = useCallback(() => {
    const id = ++seqRef.current;
    setError(null);
    void run(id);
  }, [run]);

  // Initial fetch. Runs again on dep change, bumping the sequence first so any
  // in-flight load for the previous deps is discarded. The cleanup bumps the
  // sequence on unmount (and before the next run), invalidating pending loads.
  useEffect(() => {
    const id = ++seqRef.current;
    setLoading(true);
    setError(null);
    void run(id);
    return () => {
      seqRef.current += 1;
    };
  }, [run, depsKey]);

  useEffect(() => {
    if (!pollMs) return;
    const id = setInterval(() => {
      const request = ++seqRef.current;
      void run(request);
    }, pollMs);
    return () => clearInterval(id);
  }, [pollMs, run]);

  return { data, error, loading, reload };
}