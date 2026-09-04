import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import * as api from "./api";
import type { ImageResult } from "./api";
import { Duplicates } from "./Duplicates";

function img(id: string): ImageResult {
  return {
    id, path: `/imgs/${id}.png`, thumbnail_url: `/api/thumbnail/${id}`,
    ocr_text: "", objects: [], width: 10, height: 10, format: "PNG",
    size: 2048, mtime: 1, date_taken: 1, indexed_at: 1,
  };
}

describe("Duplicates", () => {
  it("scans on open and opens a clicked image", async () => {
    const spy = vi.spyOn(api, "fetchDuplicates").mockResolvedValue([[img("a"), img("b")]]);
    const onOpen = vi.fn();
    render(<Duplicates onOpen={onOpen} />);

    expect(spy).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Find duplicates" }));

    expect(await screen.findByText("1 group of near-identical images")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /a\.png/ }));
    expect(onOpen).toHaveBeenCalledWith(expect.objectContaining({ id: "a" }));
  });

  it("shows an empty state when nothing is near-identical", async () => {
    vi.spyOn(api, "fetchDuplicates").mockResolvedValue([]);
    render(<Duplicates onOpen={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Find duplicates" }));
    await waitFor(() => expect(screen.getByText("No near-duplicates found.")).toBeInTheDocument());
  });
});
