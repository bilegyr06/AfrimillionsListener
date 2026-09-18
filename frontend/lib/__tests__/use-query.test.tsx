// @vitest-environment jsdom

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useQuery, type QueryState } from "../use-query";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

interface HarnessProps<T> {
  loader: () => Promise<T>;
  deps: unknown[];
  pollMs?: number;
}

interface Harness<T> {
  getState: () => QueryState<T>;
  rerender: (props: Partial<HarnessProps<T>>) => Promise<void>;
  unmount: () => Promise<void>;
}

async function mount<T>(props: HarnessProps<T>): Promise<Harness<T>> {
  const container = document.createElement("div");
  const root = createRoot(container);
  let current = { ...props };
  let latest: QueryState<T> | null = null;

  function Probe() {
    latest = useQuery(current.loader, current.deps, current.pollMs);
    return null;
  }

  await act(async () => {
    root.render(<Probe />);
  });

  return {
    getState: () => latest as QueryState<T>,
    rerender: async (next) => {
      current = { ...current, ...next };
      await act(async () => {
        root.render(<Probe />);
      });
    },
    unmount: async () => {
      await act(async () => {
        root.unmount();
      });
    },
  };
}

describe("useQuery lifecycle", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("loads on mount: loading while pending, then data without error", async () => {
    const load = deferred<string>();
    const loader = vi.fn(() => load.promise);
    const h = await mount({ loader, deps: [] });

    expect(h.getState()).toEqual({ data: null, error: null, loading: true, reload: expect.any(Function) });

    await act(async () => {
      load.resolve("ok");
    });

    expect(h.getState()).toMatchObject({ data: "ok", error: null, loading: false });
    expect(loader).toHaveBeenCalledTimes(1);
  });

  it("surfaces a load error as its message and clears loading", async () => {
    const load = deferred<string>();
    const h = await mount({ loader: () => load.promise, deps: [] });

    await act(async () => {
      load.reject(new Error("boom"));
    });

    expect(h.getState()).toMatchObject({ data: null, error: "boom", loading: false });
  });

  it("normalises non-Error rejections", async () => {
    const load = deferred<string>();
    const h = await mount({ loader: () => load.promise, deps: [] });

    await act(async () => {
      load.reject("raw failure");
    });

    expect(h.getState()).toMatchObject({ data: null, error: "Something went wrong.", loading: false });
  });

  it("reload refetches without touching loading and clears error eagerly", async () => {
    let load = deferred<string>();
    const loader = vi.fn(() => load.promise);
    const h = await mount({ loader, deps: [] });

    await act(async () => {
      load.resolve("first");
    });
    expect(h.getState()).toMatchObject({ data: "first", loading: false });

    load = deferred<string>();
    await act(async () => {
      h.getState().reload();
    });

    expect(h.getState()).toMatchObject({ data: "first", error: null, loading: false });

    await act(async () => {
      load.resolve("second");
    });

    expect(h.getState()).toMatchObject({ data: "second", error: null, loading: false });
    expect(loader).toHaveBeenCalledTimes(2);
  });

  it("keeps stale data on a failed reload, with the error message", async () => {
    let load = deferred<string>();
    const h = await mount({ loader: () => load.promise, deps: [] });

    await act(async () => {
      load.resolve("first");
    });

    load = deferred<string>();
    await act(async () => {
      h.getState().reload();
    });
    await act(async () => {
      load.reject(new Error("still loading"));
    });

    expect(h.getState()).toMatchObject({ data: "first", error: "still loading", loading: false });
  });

  it("refetches on dep change, keeping stale data while loading", async () => {
    let load = deferred<string>();
    const loader = vi.fn(() => load.promise);
    const h = await mount({ loader, deps: [] });

    await act(async () => {
      load.resolve("A");
    });
    expect(h.getState()).toMatchObject({ data: "A", loading: false });

    load = deferred<string>();
    await h.rerender({ deps: [2] });

    expect(h.getState()).toMatchObject({ data: "A", loading: true, error: null });

    await act(async () => {
      load.resolve("B");
    });

    expect(h.getState()).toMatchObject({ data: "B", loading: false });
    expect(loader).toHaveBeenCalledTimes(2);
  });

  it("polls silently: updates data without toggling loading", async () => {
    let load = deferred<string>();
    const loader = vi.fn(() => load.promise);
    const h = await mount({ loader, deps: [], pollMs: 15000 });
    await act(async () => {
      load.resolve("v0");
    });
    expect(h.getState()).toMatchObject({ data: "v0", loading: false });

    load = deferred<string>();
    await act(async () => {
      vi.advanceTimersByTime(15000);
    });

    expect(h.getState()).toMatchObject({ data: "v0", loading: false });
    expect(loader).toHaveBeenCalledTimes(2);

    await act(async () => {
      load.resolve("v1");
    });
    expect(h.getState()).toMatchObject({ data: "v1", loading: false });
  });

  it("drops out-of-order poll responses so the newest fetch wins", async () => {
    let load = deferred<string>();
    const loader = vi.fn(() => load.promise);
    const h = await mount({ loader, deps: [], pollMs: 15000 });
    await act(async () => {
      load.resolve("v0");
    });

    load = deferred<string>();
    await act(async () => {
      vi.advanceTimersByTime(15000);
    });
    const slow = load;

    load = deferred<string>();
    await act(async () => {
      vi.advanceTimersByTime(15000);
    });

    await act(async () => {
      load.resolve("v2");
    });
    await act(async () => {
      slow.resolve("v1");
    });

    expect(h.getState()).toMatchObject({ data: "v2" });
    expect(loader).toHaveBeenCalledTimes(3);
  });

  it("drops a stale response for previous deps after deps change", async () => {
    const staleLoad = deferred<string>();
    const loader = vi.fn(() => staleLoad.promise);
    const h = await mount({ loader, deps: [1] });

    let latest = deferred<string>();
    loader.mockImplementation(() => latest.promise);
    await h.rerender({ deps: [2] });

    await act(async () => {
      latest.resolve("B");
    });
    await act(async () => {
      staleLoad.resolve("A");
    });

    expect(h.getState()).toMatchObject({ data: "B", error: null });
    expect(loader).toHaveBeenCalledTimes(2);
  });

  it("cancels in-flight work and stops polling on unmount", async () => {
    let load = deferred<string>();
    const loader = vi.fn(() => load.promise);
    const h = await mount({ loader, deps: [], pollMs: 15000 });
    await act(async () => {
      load.resolve("v0");
    });

    load = deferred<string>();
    await act(async () => {
      vi.advanceTimersByTime(15000);
    });

    await h.unmount();
    await act(async () => {
      vi.advanceTimersByTime(45000);
    });
    expect(loader).toHaveBeenCalledTimes(2);

    await expect(act(async () => load.resolve("late"))).resolves.toBeUndefined();
  });

  it("clears a prior error when a poll succeeds", async () => {
    let load = deferred<string>();
    const loader = vi.fn(() => load.promise);
    const h = await mount({ loader, deps: [], pollMs: 15000 });
    await act(async () => {
      load.resolve("v0");
    });

    load = deferred<string>();
    await act(async () => {
      h.getState().reload();
    });
    await act(async () => {
      load.reject(new Error("transient"));
    });
    expect(h.getState()).toMatchObject({ error: "transient", data: "v0" });

    load = deferred<string>();
    await act(async () => {
      vi.advanceTimersByTime(15000);
    });
    await act(async () => {
      load.resolve("v1");
    });

    expect(h.getState()).toMatchObject({ data: "v1", error: null });
  });
});