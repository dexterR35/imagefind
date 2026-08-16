import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Pagination } from "./Pagination";

describe("Pagination", () => {
  it("does not render when all results fit on one page", () => {
    const { container } = render(<Pagination page={1} pageCount={1} onChange={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("navigates with page numbers and previous/next buttons", () => {
    const onChange = vi.fn();
    render(<Pagination page={2} pageCount={4} onChange={onChange} />);

    expect(screen.getByLabelText("Page 2")).toHaveAttribute("aria-current", "page");
    fireEvent.click(screen.getByText("Previous"));
    fireEvent.click(screen.getByLabelText("Page 4"));
    fireEvent.click(screen.getByText("Next"));

    expect(onChange.mock.calls).toEqual([[1], [4], [3]]);
  });

  it("condenses a long list of pages", () => {
    render(<Pagination page={6} pageCount={12} onChange={vi.fn()} />);

    expect(screen.getAllByText("…")).toHaveLength(2);
    expect(screen.getByLabelText("Page 1")).toBeInTheDocument();
    expect(screen.getByLabelText("Page 12")).toBeInTheDocument();
  });
});
