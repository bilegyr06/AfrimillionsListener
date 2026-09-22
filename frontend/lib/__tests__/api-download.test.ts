import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, apiDownload, filenameFromContentDisposition } from "../api";

function stubFetch(res: Response): void {
  globalThis.fetch = vi.fn(async () => res) as unknown as typeof fetch;
}

function okResponse(contentDisposition: string | null, bytes: string = "xlsx"): Response {
  const headers = new Headers();
  if (contentDisposition) headers.set("content-disposition", contentDisposition);
  return {
    ok: true,
    status: 200,
    headers,
    blob: async () => new Blob([bytes], { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" }),
  } as unknown as Response;
}

function errorResponse(status: number, detail: string): Response {
  return {
    ok: false,
    status,
    headers: new Headers(),
    json: async () => ({ detail }),
  } as unknown as Response;
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("filenameFromContentDisposition", () => {
  it("extracts the quoted backend filename", () => {
    expect(
      filenameFromContentDisposition('attachment; filename="Afrimillions_Test_Window1_Evaluation.xlsx"'),
    ).toBe("Afrimillions_Test_Window1_Evaluation.xlsx");
  });

  it("returns null when the header is absent or carries no filename", () => {
    expect(filenameFromContentDisposition(null)).toBeNull();
    expect(filenameFromContentDisposition("attachment")).toBeNull();
  });
});

describe("apiDownload", () => {
  it("fetches the prefixed path and returns the body blob with the server filename", async () => {
    stubFetch(okResponse('attachment; filename="Afrimillions_Test_Window1_Evaluation.xlsx"', "xlsx"));

    const result = await apiDownload("/windows/1/export");

    expect(globalThis.fetch as ReturnType<typeof vi.fn>).toHaveBeenCalledWith(
      "/api/windows/1/export",
      expect.objectContaining({ cache: "no-store" }),
    );
    expect(result.filename).toBe("Afrimillions_Test_Window1_Evaluation.xlsx");
    expect(result.blob).toBeInstanceOf(Blob);
  });

  it("falls back to a default filename when the server sends none", async () => {
    stubFetch(okResponse(null));
    const result = await apiDownload("/windows/1/export");
    expect(result.filename).toBe("window-export.xlsx");
  });

  it("normalises backend errors into an ApiError with the detail message", async () => {
    stubFetch(errorResponse(404, "Campaign Window #9 not found."));

    const err = await apiDownload("/windows/9/export").catch((e: unknown) => e);

    expect(err).toBeInstanceOf(ApiError);
    const apiErr = err as ApiError;
    expect(apiErr.status).toBe(404);
    expect(apiErr.message).toBe("Campaign Window #9 not found.");
  });
});