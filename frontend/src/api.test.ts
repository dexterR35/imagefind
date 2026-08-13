import { describe, expect, it, vi } from "vitest";
import { findSimilar, search } from "./api";

describe("search", () => {
  it("builds query params only for provided filters", async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => [
        { id: "a1", path: "/x.png", thumbnail_url: "/thumbnail/a1", ocr_text: "", colors: [], objects: [] },
      ],
    });
    vi.stubGlobal("fetch", mockFetch);

    const results = await search({ color: "green" });

    expect(mockFetch).toHaveBeenCalledWith("http://localhost:8000/search?color=green");
    expect(results[0].id).toBe("a1");
  });

  it("resolves thumbnail_url to an absolute backend URL, not an origin-relative path", async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => [
        { id: "a1", path: "/x.png", thumbnail_url: "/thumbnail/a1", ocr_text: "", colors: [], objects: [] },
      ],
    });
    vi.stubGlobal("fetch", mockFetch);

    const results = await search({});

    expect(results[0].thumbnail_url).toBe("http://localhost:8000/thumbnail/a1");
  });

  it("throws when the response is not ok", async () => {
    const mockFetch = vi.fn().mockResolvedValue({ ok: false, status: 500, json: async () => ({}) });
    vi.stubGlobal("fetch", mockFetch);

    await expect(search({})).rejects.toThrow();
  });
});

describe("findSimilar", () => {
  it("resolves thumbnail_url to an absolute backend URL", async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => [
        { id: "b1", path: "/y.png", thumbnail_url: "/thumbnail/b1", ocr_text: "", colors: [], objects: [] },
      ],
    });
    vi.stubGlobal("fetch", mockFetch);

    const results = await findSimilar("a1");

    expect(results[0].thumbnail_url).toBe("http://localhost:8000/thumbnail/b1");
  });
});
