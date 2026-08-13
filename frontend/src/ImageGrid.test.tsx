import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ImageGrid } from "./ImageGrid";
import type { ImageResult } from "./api";

const sample: ImageResult[] = [
  { id: "a1", path: "/imgs/clover.png", thumbnail_url: "/thumbnail/a1", ocr_text: "", colors: ["green"], objects: ["clover"] },
];

describe("ImageGrid", () => {
  it("renders a card per image and reports clicks", () => {
    const onSelect = vi.fn();
    render(<ImageGrid images={sample} onSelect={onSelect} />);
    fireEvent.click(screen.getByAltText("clover.png"));
    expect(onSelect).toHaveBeenCalledWith(sample[0]);
  });

  it("shows an empty state with no results", () => {
    render(<ImageGrid images={[]} onSelect={vi.fn()} />);
    expect(screen.getByText("No images match these filters.")).toBeInTheDocument();
  });
});
