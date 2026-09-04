import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ImageGrid } from "./ImageGrid";
import type { ImageResult } from "./api";

const sample: ImageResult[] = [
  {
    id: "a1", path: "/imgs/clover.png", thumbnail_url: "/thumbnail/a1", ocr_text: "", objects: ["clover"],
    width: 1920, height: 1080, format: "PNG", size: 2048,
    mtime: 1, date_taken: 1, indexed_at: 2,
  },
];

describe("ImageGrid", () => {
  it("renders a card per image and reports clicks", () => {
    const onSelect = vi.fn();
    render(<ImageGrid images={sample} onSelect={onSelect} />);
    expect(screen.getByText(/PNG.*2.00 KB.*Added/)).toBeInTheDocument();
    fireEvent.click(screen.getByAltText("clover.png"));
    expect(onSelect).toHaveBeenCalledWith(sample[0]);
  });

  it("shows an empty state with no results", () => {
    render(<ImageGrid images={[]} onSelect={vi.fn()} />);
    expect(screen.getByText("No images match these filters.")).toBeInTheDocument();
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
});
