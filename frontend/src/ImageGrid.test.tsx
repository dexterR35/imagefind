import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ImageGrid } from "./ImageGrid";
import type { ImageResult } from "./api";

const sample: ImageResult[] = [
  {
    id: "a1", path: "/imgs/clover.png", thumbnail_url: "/thumbnail/a1", ocr_text: "", objects: ["clover"],
    width: 1920, height: 1080, format: "PNG", size: 2048,
    mtime: 1, date_taken: 1, indexed_at: 2, added_at: 3,
  },
];

describe("ImageGrid", () => {
  it("renders a card per image and reports clicks", () => {
    const onSelect = vi.fn();
    render(<ImageGrid images={sample} onSelect={onSelect} />);
    expect(screen.getByText("PNG")).toBeInTheDocument();
    expect(screen.getByText(/1920 × 1080.*2.00 KB.*Added/)).toBeInTheDocument();
    fireEvent.click(screen.getByAltText("clover.png"));
    expect(onSelect).toHaveBeenCalledWith(sample[0]);
  });

  it("shows an empty state with no results", () => {
    render(<ImageGrid images={[]} onSelect={vi.fn()} />);
    expect(screen.getByText("No images match these filters.")).toBeInTheDocument();
  });

  it("renders a favorite toggle per card that reports without opening the modal", () => {
    const onSelect = vi.fn();
    const onToggleFavorite = vi.fn();
    render(<ImageGrid images={sample} onSelect={onSelect} onToggleFavorite={onToggleFavorite} />);

    fireEvent.click(screen.getByRole("button", { name: "Add to favorites" }));
    expect(onToggleFavorite).toHaveBeenCalledWith("a1", true);
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("renders a selection checkbox per card that toggles without opening the modal", () => {
    const onSelect = vi.fn();
    const onToggleSelect = vi.fn();
    render(
      <ImageGrid
        images={sample}
        onSelect={onSelect}
        selectedIds={new Set()}
        onToggleSelect={onToggleSelect}
      />,
    );

    fireEvent.click(screen.getByRole("checkbox", { name: "Select clover.png" }));
    expect(onToggleSelect).toHaveBeenCalledWith("a1");
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("renders a table with a row per image in table view and reports clicks", () => {
    const onSelect = vi.fn();
    render(<ImageGrid images={sample} view="table" onSelect={onSelect} />);

    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Dimensions" })).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "1920 × 1080" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "clover.png" }));
    expect(onSelect).toHaveBeenCalledWith(sample[0]);
  });

  it("shows only the filename for Windows NAS paths", () => {
    const windowsImage = { ...sample[0], path: "Z:\\campaign\\Promo ™.png" };
    render(<ImageGrid images={[windowsImage]} onSelect={vi.fn()} />);

    expect(screen.getByAltText("Promo ™.png")).toBeInTheDocument();
    expect(screen.queryByText(windowsImage.path)).not.toBeInTheDocument();
  });

  it("splits into day-by-day sections keyed by added_at when groupByDate is set", () => {
    const day1 = new Date(2026, 8, 7, 10, 0, 0).getTime() / 1000;
    const day2 = new Date(2026, 8, 3, 10, 0, 0).getTime() / 1000;
    const images: ImageResult[] = [
      { ...sample[0], id: "a", path: "/imgs/a.png", added_at: day1 },
      { ...sample[0], id: "b", path: "/imgs/b.png", added_at: day1 + 60 },
      { ...sample[0], id: "c", path: "/imgs/c.png", added_at: day2 },
    ];
    render(<ImageGrid images={images} onSelect={vi.fn()} groupByDate />);

    const headings = screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent);
    expect(headings).toEqual(["Sep 7", "Sep 3"]);
  });

  it("groups images with no added_at under an Unknown date heading instead of Jan 1 1970", () => {
    const day1 = new Date(2026, 8, 7, 10, 0, 0).getTime() / 1000;
    const images: ImageResult[] = [
      { ...sample[0], id: "a", path: "/imgs/a.png", added_at: day1 },
      { ...sample[0], id: "b", path: "/imgs/b.png", added_at: 0 },
    ];
    render(<ImageGrid images={images} onSelect={vi.fn()} groupByDate />);

    const headings = screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent);
    expect(headings).toEqual(["Sep 7", "Unknown date"]);
    expect(screen.queryByText(/1970/)).not.toBeInTheDocument();
  });
});
