import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import * as api from "./api";
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
    fireEvent.change(await screen.findByPlaceholderText("Search text or meaning..."), {
      target: { value: "clover" },
    });

    await waitFor(() => expect(screen.getByAltText("clover.png")).toBeInTheDocument());

    fireEvent.click(screen.getByAltText("clover.png"));
    fireEvent.click(screen.getByText("Find Similar"));

    await waitFor(() => expect(api.findSimilar).toHaveBeenCalledWith("a1"));
  });
});
