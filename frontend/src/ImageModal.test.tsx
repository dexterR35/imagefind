import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ImageResult } from "./api";
import { ImageModal } from "./ImageModal";

const image: ImageResult = {
  id: "a1", path: "/imgs/clover.png", thumbnail_url: "http://localhost:8000/thumbnail/a1",
  ocr_text: "NETBET", objects: ["clover"],
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

  it("loads the full-resolution original for the preview", () => {
    render(<ImageModal image={image} onClose={() => {}} onFindSimilar={() => {}} />);
    expect(screen.getByAltText("clover.png")).toHaveAttribute("src", "/api/image/a1");
  });

  it("pages between results with the arrow keys and the nav buttons", () => {
    const onPrev = vi.fn();
    const onNext = vi.fn();
    render(
      <ImageModal image={image} onClose={() => {}} onFindSimilar={() => {}} onPrev={onPrev} onNext={onNext} />,
    );

    fireEvent.keyDown(window, { key: "ArrowRight" });
    expect(onNext).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(window, { key: "ArrowLeft" });
    expect(onPrev).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByLabelText("Next image"));
    expect(onNext).toHaveBeenCalledTimes(2);
    fireEvent.click(screen.getByLabelText("Previous image"));
    expect(onPrev).toHaveBeenCalledTimes(2);
  });

  it("wires the favorite toggle, tag editor and note field to their callbacks", () => {
    const onToggleFavorite = vi.fn();
    const onTagsChange = vi.fn();
    const onNoteChange = vi.fn();
    render(
      <ImageModal
        image={{ ...image, favorite: false, user_tags: ["draft"], note: "" }}
        onClose={() => {}}
        onFindSimilar={() => {}}
        onToggleFavorite={onToggleFavorite}
        onTagsChange={onTagsChange}
        onNoteChange={onNoteChange}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Add to favorites" }));
    expect(onToggleFavorite).toHaveBeenCalledWith("a1", true);

    const tagInput = screen.getByRole("textbox", { name: "Add a tag" });
    fireEvent.change(tagInput, { target: { value: "hero" } });
    fireEvent.keyDown(tagInput, { key: "Enter" });
    expect(onTagsChange).toHaveBeenCalledWith("a1", ["draft", "hero"]);

    const note = screen.getByRole("textbox", { name: "Note" });
    fireEvent.change(note, { target: { value: "crop this" } });
    fireEvent.blur(note);
    expect(onNoteChange).toHaveBeenCalledWith("a1", "crop this");
  });

  it("does not treat typing in the tag field as a zoom/nav shortcut", () => {
    const onNext = vi.fn();
    render(
      <ImageModal
        image={{ ...image, user_tags: [] }}
        onClose={() => {}}
        onFindSimilar={() => {}}
        onNext={onNext}
        onTagsChange={() => {}}
      />,
    );
    const tagInput = screen.getByRole("textbox", { name: "Add a tag" });
    tagInput.focus();
    fireEvent.keyDown(tagInput, { key: "ArrowRight" });
    expect(onNext).not.toHaveBeenCalled();
  });

  it("closes on Escape and does not render nav buttons at the list edges", () => {
    const onClose = vi.fn();
    render(<ImageModal image={image} onClose={onClose} onFindSimilar={() => {}} />);

    expect(screen.queryByLabelText("Next image")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Previous image")).not.toBeInTheDocument();

    fireEvent.keyDown(window, { key: "Escape" });
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
