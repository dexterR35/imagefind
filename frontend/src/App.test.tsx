import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import * as api from "./api";
import type { ImageResult } from "./api";
import App from "./App";

describe("App", () => {
  it("runs a search on filter change and opens Find Similar results", async () => {
    vi.spyOn(api, "fetchColors").mockResolvedValue(["green"]);
    vi.spyOn(api, "fetchObjects").mockResolvedValue(["clover"]);
    const image = {
      id: "a1", path: "/imgs/clover.png", thumbnail_url: "/thumbnail/a1",
      ocr_text: "", colors: ["green"], objects: ["clover"],
    };
    vi.spyOn(api, "search").mockResolvedValue([image]);
    vi.spyOn(api, "findSimilar").mockResolvedValue([image]);

    render(<App />);
    fireEvent.change(await screen.findByPlaceholderText("Search text or tags..."), {
      target: { value: "clover" },
    });

    await waitFor(() => expect(screen.getByAltText("clover.png")).toBeInTheDocument());

    fireEvent.click(screen.getByAltText("clover.png"));
    fireEvent.click(screen.getByText("Find Similar"));

    await waitFor(() => expect(api.findSimilar).toHaveBeenCalledWith("a1"));
  });

  it("shows an inline error state when search fails, and clears it once a search succeeds", async () => {
    vi.spyOn(api, "fetchColors").mockResolvedValue([]);
    vi.spyOn(api, "fetchObjects").mockResolvedValue([]);
    const searchSpy = vi.spyOn(api, "search").mockRejectedValue(new Error("boom"));

    render(<App />);

    // The initial mount-time search (with empty filters) fails too.
    await waitFor(() => expect(screen.getByText(/search failed/i)).toBeInTheDocument());

    searchSpy.mockResolvedValue([]);
    fireEvent.change(screen.getByPlaceholderText("Search text or tags..."), {
      target: { value: "clover" },
    });

    await waitFor(() => expect(screen.queryByText(/search failed/i)).not.toBeInTheDocument());
  });

  it("guards against an out-of-order search response overwriting a newer one", async () => {
    vi.spyOn(api, "fetchColors").mockResolvedValue(["green"]);
    vi.spyOn(api, "fetchObjects").mockResolvedValue([]);

    const imageA: ImageResult = {
      id: "a", path: "/imgs/a.png", thumbnail_url: "http://localhost:8000/thumbnail/a",
      ocr_text: "", colors: [], objects: [],
    };
    const imageB: ImageResult = {
      id: "b", path: "/imgs/b.png", thumbnail_url: "http://localhost:8000/thumbnail/b",
      ocr_text: "", colors: [], objects: [],
    };

    let resolveStale!: (v: ImageResult[]) => void;
    const staleResponse = new Promise<ImageResult[]>((resolve) => {
      resolveStale = resolve;
    });

    vi.spyOn(api, "search")
      .mockResolvedValueOnce([]) // initial mount search
      .mockImplementationOnce(() => staleResponse) // color -> "green": stale, resolves late
      .mockResolvedValueOnce([imageB]); // color -> undefined: fresher, resolves first

    render(<App />);
    const swatch = await screen.findByLabelText("green");

    fireEvent.click(swatch); // fires the stale (slow) request
    fireEvent.click(swatch); // fires the fresh (fast) request

    await waitFor(() => expect(screen.getByAltText("b.png")).toBeInTheDocument());

    resolveStale([imageA]);
    // Flush the now-resolved stale request's continuation (a no-op re-assertion
    // that b.png is still there is enough to let its microtask run first).
    await waitFor(() => expect(screen.getByAltText("b.png")).toBeInTheDocument());
    expect(screen.queryByAltText("a.png")).not.toBeInTheDocument();
  });
});
