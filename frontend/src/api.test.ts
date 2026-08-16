import { describe, expect, it, vi } from "vitest";
import { findSimilar, search, startReindex, updateSettings } from "./api";

describe("search", () => {
  it("builds query params only for provided filters", async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ results: [
        { id: "a1", path: "/x.png", thumbnail_url: "/thumbnail/a1", ocr_text: "", colors: [], objects: [] },
      ], total: 1 }),
    });
    vi.stubGlobal("fetch", mockFetch);

    const results = await search({ color: "green" });

    expect(mockFetch).toHaveBeenCalledWith("http://localhost:8000/search?color=green");
    expect(results.results[0].id).toBe("a1");
    expect(results.total).toBe(1);
  });

  it("resolves thumbnail_url to an absolute backend URL, not an origin-relative path", async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ results: [
        { id: "a1", path: "/x.png", thumbnail_url: "/thumbnail/a1", ocr_text: "", colors: [], objects: [] },
      ], total: 1 }),
    });
    vi.stubGlobal("fetch", mockFetch);

    const results = await search({});

    expect(results.results[0].thumbnail_url).toBe("http://localhost:8000/thumbnail/a1");
  });

  it("passes pagination and sorting options", async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ results: [], total: 0 }),
    });
    vi.stubGlobal("fetch", mockFetch);

    await search({ text: "logo" }, { sort: "name_desc", offset: 60, limit: 30 });

    expect(mockFetch).toHaveBeenCalledWith(
      "http://localhost:8000/search?text=logo&sort=name_desc&offset=60&limit=30",
    );
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

describe("startReindex", () => {
  it("passes force=true through as a query param", async () => {
    const mockFetch = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ job_id: "j1" }) });
    vi.stubGlobal("fetch", mockFetch);

    await startReindex(true);

    expect(mockFetch).toHaveBeenCalledWith("http://localhost:8000/reindex?force=true", { method: "POST" });
  });
});

describe("updateSettings", () => {
  it("POSTs the full settings object as JSON", async () => {
    const settings = { ram_confidence: 0.05, ram_custom_tags: ["zeus"], images_dir: "/photos" };
    const mockFetch = vi.fn().mockResolvedValue({ ok: true, json: async () => settings });
    vi.stubGlobal("fetch", mockFetch);

    const result = await updateSettings(settings);

    expect(mockFetch).toHaveBeenCalledWith("http://localhost:8000/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(settings),
    });
    expect(result).toEqual(settings);
  });
});
