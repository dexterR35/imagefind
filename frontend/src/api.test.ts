import { describe, expect, it, vi } from "vitest";
import { search } from "./api";

describe("search", () => {
  it("builds query params only for provided filters", async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      json: async () => [
        { id: "a1", path: "/x.png", thumbnail_url: "/thumbnail/a1", ocr_text: "", colors: [], objects: [] },
      ],
    });
    vi.stubGlobal("fetch", mockFetch);

    const results = await search({ color: "green" });

    expect(mockFetch).toHaveBeenCalledWith("http://localhost:8000/search?color=green");
    expect(results[0].id).toBe("a1");
  });
});
