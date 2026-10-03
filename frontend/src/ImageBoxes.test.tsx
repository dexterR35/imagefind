import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "./api";
import type { ImageResult } from "./api";
import { ImageModal } from "./ImageModal";

const image: ImageResult = {
  id: "a1", path: "/imgs/sheet.png", thumbnail_url: "/api/thumbnail/a1",
  ocr_text: "", objects: ["gun"],
  width: 400, height: 200, format: "PNG", size: 2048,
  mtime: 1, date_taken: 1, indexed_at: 1, added_at: 1,
};

// jsdom does no layout: give the preview image a size so the overlay mounts,
// and an on-screen rect so pointer positions map to image fractions.
beforeEach(() => {
  vi.spyOn(HTMLElement.prototype, "offsetWidth", "get").mockReturnValue(400);
  vi.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockReturnValue(200);
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue(
    { left: 0, top: 0, width: 400, height: 200, right: 400, bottom: 200, x: 0, y: 0, toJSON: () => ({}) },
  );
});
// jsdom has no pointer capture.
beforeEach(() => {
  HTMLElement.prototype.setPointerCapture = vi.fn();
  HTMLElement.prototype.releasePointerCapture = vi.fn();
  HTMLElement.prototype.hasPointerCapture = vi.fn(() => true);
});
afterEach(() => vi.restoreAllMocks());

function renderModal(props: Partial<Parameters<typeof ImageModal>[0]> = {}) {
  render(<ImageModal image={image} onClose={() => {}} onFindSimilar={() => {}} {...props} />);
  fireEvent.load(screen.getByAltText("sheet.png"));
}

describe("ImageModal boxes", () => {
  it("shows where a recognized object is when its chip is clicked", async () => {
    const detect = vi.spyOn(api, "detectInImage").mockResolvedValue([
      { label: "gun", score: 0.42, source: "word", box: [0.25, 0.5, 0.5, 1] },
    ]);
    renderModal();

    fireEvent.click(screen.getByRole("button", { name: "gun" }));

    expect(detect).toHaveBeenCalledWith("a1", "gun", expect.any(AbortSignal));
    expect(await screen.findByText("Found 1 for “gun”.")).toBeInTheDocument();
    const label = screen.getByText("gun 42%");
    expect(label.parentElement).toHaveStyle({ left: "25%", top: "50%", width: "25%", height: "50%" });

    fireEvent.click(screen.getByText("Clear boxes"));
    expect(screen.queryByText("gun 42%")).not.toBeInTheDocument();
  });

  it("reports when the word is not in the image", async () => {
    vi.spyOn(api, "detectInImage").mockResolvedValue([]);
    renderModal();

    fireEvent.change(screen.getByLabelText("Word to find in image"), { target: { value: "zeus" } });
    fireEvent.click(screen.getByRole("button", { name: "Find" }));

    expect(await screen.findByText("“zeus” not found in this image.")).toBeInTheDocument();
  });

  it("teaches a word from a drawn box and tags the image", async () => {
    const add = vi.spyOn(api, "addExample").mockResolvedValue({ tag: "zeus", examples: 2, user_tags: ["zeus"] });
    const onTagsChange = vi.fn();
    renderModal({ onTagsChange });

    fireEvent.click(screen.getByRole("button", { name: "Draw a box" }));
    const overlay = screen.getByTestId("box-overlay");
    // All three in one batch, like a fast drag that outruns re-rendering.
    act(() => {
      fireEvent.pointerDown(overlay, { pointerId: 1, clientX: 100, clientY: 20 });
      fireEvent.pointerMove(overlay, { pointerId: 1, clientX: 300, clientY: 180 });
      fireEvent.pointerUp(overlay, { pointerId: 1, clientX: 300, clientY: 180 });
    });
    fireEvent.change(screen.getByLabelText("What is in the box"), { target: { value: "zeus" } });
    fireEvent.click(screen.getByRole("button", { name: "Save example" }));

    await waitFor(() => expect(add).toHaveBeenCalledWith("a1", "zeus", [0.25, 0.1, 0.75, 0.9]));
    expect(await screen.findByText(/Saved example 2 of “zeus”/)).toBeInTheDocument();
    expect(onTagsChange).toHaveBeenCalledWith("a1", ["zeus"]);
  });

  it("Escape cancels drawing instead of closing the viewer", () => {
    const onClose = vi.fn();
    renderModal({ onClose });

    fireEvent.click(screen.getByRole("button", { name: "Draw a box" }));
    fireEvent.keyDown(window, { key: "Escape" });

    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Draw a box" })).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });
});
