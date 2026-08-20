import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ImageResult } from "./api";
import { ImageModal } from "./ImageModal";

const image: ImageResult = {
  id: "a1", path: "/imgs/clover.png", thumbnail_url: "http://localhost:8000/thumbnail/a1",
  ocr_text: "NETBET", colors: ["green"], objects: ["clover"],
  width: 1920, height: 1080, format: "PNG", size: 2048,
  mtime: 1_700_000_000, date_taken: 1_699_000_000, indexed_at: 1_700_000_100,
};

describe("ImageModal", () => {
  it("locks background scrolling while open and restores it when closed", () => {
    document.body.style.overflow = "auto";
    const { unmount } = render(
      <ImageModal image={image} onClose={() => {}} onFindSimilar={() => {}} />,
    );

    expect(document.body.style.overflow).toBe("hidden");
    unmount();
    expect(document.body.style.overflow).toBe("auto");
  });

  it("renders a download link pointing at the /download endpoint for this image", () => {
    render(<ImageModal image={image} onClose={() => {}} onFindSimilar={() => {}} />);

    const link = screen.getByText("Download original") as HTMLAnchorElement;
    expect(link.tagName).toBe("A");
    expect(link.href).toBe("http://localhost:3000/api/download/a1");
  });

  it("calls onFindSimilar and onClose from their respective buttons", () => {
    const onFindSimilar = vi.fn();
    const onClose = vi.fn();
    render(<ImageModal image={image} onClose={onClose} onFindSimilar={onFindSimilar} />);

    fireEvent.click(screen.getByText("Find Similar"));
    expect(onFindSimilar).toHaveBeenCalledWith("a1");

    fireEvent.click(screen.getByLabelText("Close"));
    expect(onClose).toHaveBeenCalled();
  });

  it("shows metadata saved for the image", () => {
    render(<ImageModal image={image} onClose={() => {}} onFindSimilar={() => {}} />);

    expect(screen.getByText("1920 × 1080 px")).toBeInTheDocument();
    expect(screen.getByText("PNG")).toBeInTheDocument();
    expect(screen.getByText("2.00 KB")).toBeInTheDocument();
    expect(screen.getByText("NETBET")).toBeInTheDocument();
  });
});
