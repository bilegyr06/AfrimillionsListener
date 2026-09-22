// @vitest-environment jsdom

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ExportEvaluation from "@/components/windows/export-evaluation";

const CONTENT_DISPOSITION = 'attachment; filename="Afrimillions_Test_Window1_Evaluation.xlsx"';

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

function stubFetch(res: unknown): void {
  globalThis.fetch = vi.fn(() => Promise.resolve(res)) as unknown as typeof fetch;
}

function baseResponse() {
  const headers = new Headers();
  headers.set("content-disposition", CONTENT_DISPOSITION);
  return {
    ok: true,
    status: 200,
    headers,
    blob: async () => new Blob(["xlsx"], { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" }),
  };
}

async function mount(flash: (f: { kind: "success" | "error"; text: string }) => void) {
  const container = document.createElement("div");
  const root = createRoot(container);
  await act(async () => {
    root.render(<ExportEvaluation windowId={1} flash={flash} />);
  });
  const button = container.querySelector("button") as HTMLButtonElement;
  return { container, root, button };
}

async function click(button: HTMLButtonElement) {
  await act(async () => {
    button.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
}

let clickedAnchor: HTMLAnchorElement | null = null;

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  clickedAnchor = null;
  URL.createObjectURL = vi.fn(() => "blob:mock") as unknown as typeof URL.createObjectURL;
  URL.revokeObjectURL = vi.fn() as unknown as typeof URL.revokeObjectURL;
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
    clickedAnchor = this;
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("ExportEvaluation", () => {
  it("downloads the evaluation via the export endpoint using the backend filename", async () => {
    const flash = vi.fn();
    stubFetch(baseResponse());
    const { button } = await mount(flash);

    await click(button);

    expect(globalThis.fetch as ReturnType<typeof vi.fn>).toHaveBeenCalledWith(
      "/api/windows/1/export",
      expect.anything(),
    );
    expect(URL.createObjectURL).toHaveBeenCalledOnce();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:mock");
    expect(clickedAnchor).not.toBeNull();
    expect(clickedAnchor?.download).toBe("Afrimillions_Test_Window1_Evaluation.xlsx");
    expect(clickedAnchor?.href).toBe("blob:mock");
    expect(flash).not.toHaveBeenCalled();
    expect(button.disabled).toBe(false);
  });

  it("shows a loading state and blocks duplicate exports while one is pending", async () => {
    const flash = vi.fn();
    const pending = deferred<Response>();
    globalThis.fetch = vi.fn(() => pending.promise) as unknown as typeof fetch;
    const { button } = await mount(flash);

    await click(button);
    expect(button.disabled).toBe(true);
    expect(button.textContent).toBe("Exporting...");

    // A second click while pending must not fire another request.
    await click(button);
    expect(globalThis.fetch as ReturnType<typeof vi.fn>).toHaveBeenCalledTimes(1);

    await act(async () => {
      pending.resolve(baseResponse() as unknown as Response);
    });

    expect(button.disabled).toBe(false);
    expect(button.textContent).toBe("Export evaluation");
  });

  it("surfaces backend errors via flash and re-enables the button without downloading", async () => {
    const flash = vi.fn();
    stubFetch({
      ok: false,
      status: 404,
      headers: new Headers(),
      json: async () => ({ detail: "Campaign Window #1 not found." }),
    });
    const { button } = await mount(flash);

    await click(button);

    expect(flash).toHaveBeenCalledWith({ kind: "error", text: "Campaign Window #1 not found." });
    expect(URL.createObjectURL).not.toHaveBeenCalled();
    expect(clickedAnchor).toBeNull();
    expect(button.disabled).toBe(false);
    expect(button.textContent).toBe("Export evaluation");
  });
});