import { describe, expect, it, vi } from "vitest";
import {
  fetchAuthSession,
  findSimilar,
  login,
  logout,
  search,
  startReindex,
  updateSettings,
} from "./api";

describe("search", () => {
  it("builds query params only for provided filters", async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ results: [
        { id: "a1", path: "/x.png", thumbnail_url: "/thumbnail/a1", ocr_text: "", objects: [] },
      ], total: 1 }),
    });
    vi.stubGlobal("fetch", mockFetch);

    const results = await search({ object: "clover" });

    expect(mockFetch).toHaveBeenCalledWith("/api/search?object=clover");
    expect(results.results[0].id).toBe("a1");
    expect(results.total).toBe(1);
  });

  it("serializes metadata facets, sending date_field only with a date bound", async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ results: [], total: 0 }),
    });
    vi.stubGlobal("fetch", mockFetch);

    await search({
      format: "png",
      sizeMin: 1_500_000,
      sizeMax: 5_000_000,
      dateField: "mtime",
      dateFrom: 1_704_067_200,
      widthMin: 800,
      heightMax: 2000,
    });

    expect(mockFetch).toHaveBeenCalledWith(
      "/api/search?format=png&size_min=1500000&size_max=5000000&date_from=1704067200" +
        "&date_field=mtime&width_min=800&height_max=2000",
    );
  });

  it("omits date_field when no date bound is set", async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ results: [], total: 0 }),
    });
    vi.stubGlobal("fetch", mockFetch);

    await search({ dateField: "indexed_at", format: "webp" });

    expect(mockFetch).toHaveBeenCalledWith("/api/search?format=webp");
  });

  it("resolves thumbnail_url through the same-origin API proxy", async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ results: [
        { id: "a1", path: "/x.png", thumbnail_url: "/thumbnail/a1", ocr_text: "", objects: [] },
      ], total: 1 }),
    });
    vi.stubGlobal("fetch", mockFetch);

    const results = await search({});

    expect(results.results[0].thumbnail_url).toBe("/api/thumbnail/a1");
  });

  it("passes pagination and sorting options", async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ results: [], total: 0 }),
    });
    vi.stubGlobal("fetch", mockFetch);

    await search({ text: "logo" }, { sort: "name_desc", offset: 60, limit: 30 });

    expect(mockFetch).toHaveBeenCalledWith(
      "/api/search?text=logo&sort=name_desc&offset=60&limit=30",
    );
  });

  it("passes an AbortSignal so stale searches can be cancelled", async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ results: [], total: 0 }),
    });
    vi.stubGlobal("fetch", mockFetch);
    const controller = new AbortController();

    await search({}, { signal: controller.signal });

    expect(mockFetch).toHaveBeenCalledWith(
      "/api/search?",
      { signal: controller.signal },
    );
  });

  it("throws when the response is not ok", async () => {
    const mockFetch = vi.fn().mockResolvedValue({ ok: false, status: 500, json: async () => ({}) });
    vi.stubGlobal("fetch", mockFetch);

    await expect(search({})).rejects.toThrow();
  });
});

describe("findSimilar", () => {
  it("resolves thumbnail_url through the same-origin API proxy", async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => [
        { id: "b1", path: "/y.png", thumbnail_url: "/thumbnail/b1", ocr_text: "", objects: [] },
      ],
    });
    vi.stubGlobal("fetch", mockFetch);

    const results = await findSimilar("a1");

    expect(results[0].thumbnail_url).toBe("/api/thumbnail/b1");
  });
});

describe("startReindex", () => {
  it("passes force=true through as a query param", async () => {
    const mockFetch = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ job_id: "j1" }) });
    vi.stubGlobal("fetch", mockFetch);

    await startReindex(true);

    expect(mockFetch).toHaveBeenCalledWith("/api/reindex?force=true", { method: "POST" });
  });
});

describe("updateSettings", () => {
  it("POSTs the full settings object as JSON", async () => {
    const settings = { ram_confidence: 0.05, ram_custom_tags: ["zeus"], images_dir: "/photos" };
    const mockFetch = vi.fn().mockResolvedValue({ ok: true, json: async () => settings });
    vi.stubGlobal("fetch", mockFetch);

    const result = await updateSettings(settings);

    expect(mockFetch).toHaveBeenCalledWith("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(settings),
    });
    expect(result).toEqual(settings);
  });
});

describe("authentication", () => {
  it("keeps the CSRF token in memory and sends it on state-changing requests", async () => {
    const mockFetch = vi.fn()
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({
          authenticated: true,
          configured: true,
          expires_at: 2_000_000_000,
          csrf_token: "csrf-123",
        }),
      })
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({ job_id: "j1" }) })
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({ status: "logged out" }) });
    vi.stubGlobal("fetch", mockFetch);

    await fetchAuthSession();
    await startReindex();
    const reindexInit = mockFetch.mock.calls[1][1] as RequestInit;
    expect(new Headers(reindexInit.headers).get("X-CSRF-Token")).toBe("csrf-123");

    await logout();
    const logoutInit = mockFetch.mock.calls[2][1] as RequestInit;
    expect(new Headers(logoutInit.headers).get("X-CSRF-Token")).toBe("csrf-123");
  });

  it("submits only the password to the login endpoint", async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ authenticated: true, configured: true, csrf_token: "csrf-login" }),
    });
    vi.stubGlobal("fetch", mockFetch);

    await login("a private password");

    expect(mockFetch).toHaveBeenCalledWith("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: "a private password" }),
    });
    await logout();
  });
});
