import { describe, expect, it, vi } from "vitest";
import {
  addToCollection,
  bulkAddTags,
  bulkSetFavorite,
  createBackup,
  createCollection,
  deleteCollection,
  exportUrl,
  fetchAuthSession,
  fetchBackups,
  fetchDuplicates,
  fetchStats,
  filtersToSearchParams,
  findSimilar,
  imageUrl,
  login,
  logout,
  removeFromCollection,
  renameCollection,
  search,
  searchParamsToFilters,
  setFavorite,
  setImageNote,
  setImageTags,
  startReindex,
  streamReindexStatus,
  updateSettings,
  zipDownloadUrl,
} from "./api";

function okFetch(body: unknown) {
  return vi.fn().mockResolvedValue({ ok: true, json: async () => body });
}

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

  it("passes mode=semantic through and round-trips it in the URL", async () => {
    const mockFetch = okFetch({ results: [], total: 0 });
    vi.stubGlobal("fetch", mockFetch);

    await search({ text: "a sunset over water", mode: "semantic" });
    expect(mockFetch).toHaveBeenCalledWith(
      "/api/search?text=a+sunset+over+water&mode=semantic",
    );
    expect(searchParamsToFilters(new URLSearchParams("text=x&mode=semantic"))).toEqual({
      text: "x",
      mode: "semantic",
    });
  });

  it("passes the orientation facet through", async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ results: [], total: 0 }),
    });
    vi.stubGlobal("fetch", mockFetch);

    await search({ orientation: "portrait" });

    expect(mockFetch).toHaveBeenCalledWith("/api/search?orientation=portrait");
  });

  it("round-trips a filter set through the URL param helpers", () => {
    const filters = {
      text: "clover",
      object: "cat",
      format: "png",
      orientation: "landscape" as const,
      dateField: "mtime" as const,
      dateFrom: 1_704_067_200,
      sizeMin: 1_500_000,
      widthMin: 800,
      favorite: true,
      collection: "col123",
      userTag: "hero",
    };
    const params = filtersToSearchParams(filters);

    expect(searchParamsToFilters(params)).toEqual(filters);
    // Junk values are dropped, not thrown.
    expect(searchParamsToFilters(new URLSearchParams("orientation=sideways&date_field=nope&width_min=-5")))
      .toEqual({});
  });

  it("sends the favorite / collection / user_tag facets", async () => {
    const mockFetch = okFetch({ results: [], total: 0 });
    vi.stubGlobal("fetch", mockFetch);

    await search({ favorite: true, collection: "c1", userTag: "hero" });

    expect(mockFetch).toHaveBeenCalledWith(
      "/api/search?favorite=true&collection=c1&user_tag=hero",
    );
  });
});

describe("curation api", () => {
  it("PUTs favorite / tags / note to the per-image endpoints", async () => {
    const mockFetch = okFetch({ favorite: true });
    vi.stubGlobal("fetch", mockFetch);
    await expect(setFavorite("a1", true)).resolves.toBe(true);
    expect(mockFetch).toHaveBeenCalledWith(
      "/api/images/a1/favorite",
      expect.objectContaining({ method: "PUT", body: JSON.stringify({ favorite: true }) }),
    );

    vi.stubGlobal("fetch", okFetch({ user_tags: ["x"] }));
    await expect(setImageTags("a1", ["x"])).resolves.toEqual(["x"]);

    vi.stubGlobal("fetch", okFetch({ note: "hi" }));
    await expect(setImageNote("a1", "hi")).resolves.toBe("hi");
  });

  it("drives the collection endpoints", async () => {
    vi.stubGlobal("fetch", okFetch({ id: "c1", name: "C", created_at: 0, count: 0 }));
    await expect(createCollection("C")).resolves.toMatchObject({ id: "c1" });

    const mockFetch = okFetch({ added: 2 });
    vi.stubGlobal("fetch", mockFetch);
    await expect(addToCollection("c1", ["a", "b"])).resolves.toBe(2);
    expect(mockFetch).toHaveBeenCalledWith(
      "/api/collections/c1/images",
      expect.objectContaining({ method: "POST", body: JSON.stringify({ image_ids: ["a", "b"] }) }),
    );

    vi.stubGlobal("fetch", okFetch({ removed: 1 }));
    await expect(removeFromCollection("c1", ["a"])).resolves.toBe(1);

    const patchFetch = okFetch({});
    vi.stubGlobal("fetch", patchFetch);
    await renameCollection("c1", "New");
    expect(patchFetch).toHaveBeenCalledWith(
      "/api/collections/c1",
      expect.objectContaining({ method: "PATCH" }),
    );

    const delFetch = okFetch({});
    vi.stubGlobal("fetch", delFetch);
    await deleteCollection("c1");
    expect(delFetch).toHaveBeenCalledWith(
      "/api/collections/c1",
      expect.objectContaining({ method: "DELETE" }),
    );
  });
});

describe("imageUrl", () => {
  it("points at the full-resolution /image endpoint through the proxy", () => {
    expect(imageUrl("abc")).toBe("/api/image/abc");
  });
});

describe("exportUrl", () => {
  it("builds a download URL from the current filters, sort and output format", () => {
    expect(exportUrl({ text: "clover", favorite: true }, "name_asc", "csv")).toBe(
      "/api/search/export?text=clover&favorite=true&sort=name_asc&output=csv",
    );
    // Default sort is omitted, JSON output is explicit.
    expect(exportUrl({}, "date_desc", "json")).toBe("/api/search/export?output=json");
  });
});

describe("duplicates + reindex stream", () => {
  it("fetches duplicate groups and resolves thumbnail URLs", async () => {
    const mockFetch = okFetch([
      [{ id: "a", path: "/x/a.png", thumbnail_url: "/thumbnail/a", ocr_text: "", objects: [] }],
    ]);
    vi.stubGlobal("fetch", mockFetch);

    const groups = await fetchDuplicates();
    expect(mockFetch).toHaveBeenCalledWith("/api/duplicates?threshold=0.08");
    expect(groups[0][0].thumbnail_url).toBe("/api/thumbnail/a");
  });

  it("streamReindexStatus returns null when EventSource is unavailable", () => {
    expect(typeof EventSource).toBe("undefined");
    expect(streamReindexStatus("job1", () => {}, () => {})).toBeNull();
  });
});

describe("bulk actions api", () => {
  it("POSTs bulk favorite and tag-add, and builds the zip URL", async () => {
    const fav = okFetch({ changed: 2 });
    vi.stubGlobal("fetch", fav);
    await expect(bulkSetFavorite(["a", "b"], true)).resolves.toBe(2);
    expect(fav).toHaveBeenCalledWith(
      "/api/images/favorite",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ image_ids: ["a", "b"], favorite: true }),
      }),
    );

    vi.stubGlobal("fetch", okFetch({ added: 4 }));
    await expect(bulkAddTags(["a", "b"], ["hero"])).resolves.toBe(4);

    expect(zipDownloadUrl(["a", "b", "c"])).toBe("/api/download/zip?ids=a,b,c");
  });
});

describe("backups api", () => {
  it("GETs and POSTs /backup", async () => {
    const list = okFetch([{ name: "index-x.db", size: 10, created_at: 1 }]);
    vi.stubGlobal("fetch", list);
    await expect(fetchBackups()).resolves.toHaveLength(1);
    expect(list).toHaveBeenCalledWith("/api/backup");

    const create = okFetch({ name: "index-y.db", size: 20, created_at: 2 });
    vi.stubGlobal("fetch", create);
    await expect(createBackup()).resolves.toMatchObject({ name: "index-y.db" });
    expect(create).toHaveBeenCalledWith("/api/backup", expect.objectContaining({ method: "POST" }));
  });
});

describe("fetchStats", () => {
  it("GETs /stats and returns the parsed body", async () => {
    const body = { total: 3, total_size: 99, by_format: [], by_year: [] };
    const mockFetch = vi.fn().mockResolvedValue({ ok: true, json: async () => body });
    vi.stubGlobal("fetch", mockFetch);

    await expect(fetchStats()).resolves.toEqual(body);
    expect(mockFetch).toHaveBeenCalledWith("/api/stats");
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
