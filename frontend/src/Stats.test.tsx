import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import * as api from "./api";
import { Stats } from "./Stats";

const sample: api.CatalogStats = {
  total: 1234,
  total_size: 5_000_000,
  indexed_at_min: 1_700_000_000,
  indexed_at_max: 1_700_500_000,
  by_format: [
    { format: "PNG", count: 1000 },
    { format: "JPEG", count: 234 },
  ],
  by_year: [{ year: "2024", count: 1200 }, { year: "Unknown", count: 34 }],
  with_ocr_text: 800,
  with_objects: 1100,
  without_ocr_or_objects: 50,
  largest: [{ id: "x", path: "/imgs/huge.png", size: 4_000_000 }],
};

describe("Stats", () => {
  it("fetches and renders catalog aggregates only after it is opened", async () => {
    const spy = vi.spyOn(api, "fetchStats").mockResolvedValue(sample);
    render(<Stats />);

    expect(spy).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Open stats" }));

    expect(await screen.findByText("1,234")).toBeInTheDocument();
    expect(screen.getByText("By format")).toBeInTheDocument();
    expect(screen.getByText("huge.png")).toBeInTheDocument();
  });

  it("shows an error when the request fails", async () => {
    vi.spyOn(api, "fetchStats").mockRejectedValue(new Error("offline"));
    render(<Stats />);

    fireEvent.click(screen.getByRole("button", { name: "Open stats" }));

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/could not load stats/i));
  });
});
