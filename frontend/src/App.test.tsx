import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "./api";
import type { ImageResult } from "./api";
import App from "./App";

function image(id: string): ImageResult {
  return {
    id,
    path: `/imgs/${id}.png`,
    thumbnail_url: `http://localhost:8000/thumbnail/${id}`,
    ocr_text: "",
    objects: [],
    width: 100,
    height: 80,
    format: "PNG",
    size: 2048,
    mtime: 1_700_000_000,
    date_taken: 1_700_000_000,
    indexed_at: 1_700_000_100,
  };
}

describe("App", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, "fetchAuthSession").mockResolvedValue({
      authenticated: true,
      configured: true,
      expires_at: 2_000_000_000,
      csrf_token: "test-csrf",
    });
  });

  it("runs a search on filter change and opens Find Similar results", async () => {
    vi.spyOn(api, "fetchObjects").mockResolvedValue([]);
    const result = { ...image("a1"), path: "/imgs/clover.png", objects: ["clover"] };
    vi.spyOn(api, "search").mockResolvedValue({ results: [result], total: 1 });
    vi.spyOn(api, "findSimilar").mockResolvedValue([result]);

    render(<App />);
    fireEvent.change(await screen.findByRole("textbox", { name: "Search images" }), {
      target: { value: "clover" },
    });

    await waitFor(() => expect(screen.getByAltText("clover.png")).toBeInTheDocument());

    fireEvent.click(screen.getByAltText("clover.png"));
    fireEvent.click(screen.getByText("Find Similar"));

    await waitFor(() => expect(api.findSimilar).toHaveBeenCalledWith("a1", expect.anything()));
  });

  it("shows an inline error state when search fails, and clears it once a search succeeds", async () => {
    vi.spyOn(api, "fetchObjects").mockResolvedValue([]);
    const searchSpy = vi.spyOn(api, "search").mockRejectedValue(new Error("boom"));

    render(<App />);

    // The initial mount-time search (with empty filters) fails too.
    await waitFor(() => expect(screen.getByText(/search failed/i)).toBeInTheDocument());

    searchSpy.mockResolvedValue({ results: [], total: 0 });
    fireEvent.change(screen.getByRole("textbox", { name: "Search images" }), {
      target: { value: "clover" },
    });

    await waitFor(() => expect(screen.queryByText(/search failed/i)).not.toBeInTheDocument());
  });

  it("guards against an out-of-order search response overwriting a newer one", async () => {
    vi.spyOn(api, "fetchObjects").mockResolvedValue([]);

    const imageA = image("a");
    const imageB = image("b");

    let resolveStale!: (v: api.SearchResponse) => void;
    const staleResponse = new Promise<api.SearchResponse>((resolve) => {
      resolveStale = resolve;
    });

    vi.spyOn(api, "search")
      .mockResolvedValueOnce({ results: [], total: 0 }) // initial mount search
      .mockImplementationOnce(() => staleResponse) // first sort: stale, resolves late
      .mockResolvedValueOnce({ results: [imageB], total: 1 }); // second sort: fresher, resolves first

    render(<App />);
    const sortControl = await screen.findByLabelText("Sort by");

    fireEvent.change(sortControl, { target: { value: "name_asc" } }); // stale request
    await waitFor(() => expect(api.search).toHaveBeenCalledTimes(2));
    fireEvent.change(sortControl, { target: { value: "name_desc" } }); // fresh request

    await waitFor(() => expect(screen.getByAltText("b.png")).toBeInTheDocument());

    resolveStale({ results: [imageA], total: 1 });
    // Flush the now-resolved stale request's continuation (a no-op re-assertion
    // that b.png is still there is enough to let its microtask run first).
    await waitFor(() => expect(screen.getByAltText("b.png")).toBeInTheDocument());
    expect(screen.queryByAltText("a.png")).not.toBeInTheDocument();
  });

  it("requests another page and applies the selected sort", async () => {
    vi.spyOn(api, "fetchObjects").mockResolvedValue([]);
    const searchSpy = vi.spyOn(api, "search")
      .mockResolvedValueOnce({ results: [image("first")], total: 61 })
      .mockResolvedValueOnce({ results: [image("last")], total: 61 })
      .mockResolvedValueOnce({ results: [image("sorted")], total: 61 });
    vi.stubGlobal("scrollTo", vi.fn());

    render(<App />);
    await screen.findByAltText("first.png");

    fireEvent.click(screen.getByText("Next"));
    await screen.findByAltText("last.png");
    expect(searchSpy).toHaveBeenLastCalledWith({}, {
      sort: "date_desc", offset: 60, limit: 60, signal: expect.anything(),
    });

    fireEvent.change(screen.getByLabelText("Sort by"), { target: { value: "name_asc" } });
    await screen.findByAltText("sorted.png");
    expect(searchSpy).toHaveBeenLastCalledWith({}, {
      sort: "name_asc", offset: 0, limit: 60, signal: expect.anything(),
    });
  });

  it("shows setup instructions when no shared password exists", async () => {
    vi.mocked(api.fetchAuthSession).mockResolvedValue({ authenticated: false, configured: false });

    render(<App />);

    expect(await screen.findByText(/authentication has not been configured/i)).toBeInTheDocument();
    expect(screen.getByText("npm run auth:set-password")).toBeInTheDocument();
  });

  it("logs in with the shared password and can log out", async () => {
    vi.mocked(api.fetchAuthSession).mockResolvedValue({ authenticated: false, configured: true });
    vi.spyOn(api, "login").mockResolvedValue({
      authenticated: true,
      configured: true,
      expires_at: 2_000_000_000,
      csrf_token: "csrf-after-login",
    });
    vi.spyOn(api, "logout").mockResolvedValue();
    vi.spyOn(api, "fetchObjects").mockResolvedValue([]);
    vi.spyOn(api, "search").mockResolvedValue({ results: [], total: 0 });

    render(<App />);
    const password = await screen.findByLabelText("Shared password");
    fireEvent.change(password, { target: { value: "correct horse battery staple" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

    const logoutButton = await screen.findByRole("button", { name: "Log out" });
    expect(api.login).toHaveBeenCalledWith("correct horse battery staple");
    fireEvent.click(logoutButton);

    await waitFor(() => expect(api.logout).toHaveBeenCalled());
    expect(await screen.findByLabelText("Shared password")).toBeInTheDocument();
  });
});
