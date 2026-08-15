import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ImageResult } from "./api";
import { ImageModal } from "./ImageModal";

const image: ImageResult = {
  id: "a1", path: "/imgs/clover.png", thumbnail_url: "http://localhost:8000/thumbnail/a1",
  ocr_text: "NETBET", colors: ["green"], objects: ["clover"],
};

describe("ImageModal", () => {
  it("renders a download link pointing at the /download endpoint for this image", () => {
    render(<ImageModal image={image} onClose={() => {}} onFindSimilar={() => {}} />);

    const link = screen.getByText("Download") as HTMLAnchorElement;
    expect(link.tagName).toBe("A");
    expect(link.href).toBe("http://localhost:8000/download/a1");
  });

  it("calls onFindSimilar and onClose from their respective buttons", () => {
    const onFindSimilar = vi.fn();
    const onClose = vi.fn();
    render(<ImageModal image={image} onClose={onClose} onFindSimilar={onFindSimilar} />);

    fireEvent.click(screen.getByText("Find Similar"));
    expect(onFindSimilar).toHaveBeenCalledWith("a1");

    fireEvent.click(screen.getByText("Close"));
    expect(onClose).toHaveBeenCalled();
  });
});
