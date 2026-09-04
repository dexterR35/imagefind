import { act, render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import * as api from "./api";
import { SearchFilters } from "./SearchFilters";

describe("SearchFilters", () => {
  it("loads objects and reports combined filter changes", async () => {
    vi.spyOn(api, "fetchObjects").mockResolvedValue(["clover", "person"]);
    const onChange = vi.fn();

    render(<SearchFilters onChange={onChange} />);

    const objectSelect = await screen.findByRole("combobox", { name: "Filter by object" });
    fireEvent.change(objectSelect, { target: { value: "clover" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Search images" }), {
      target: { value: "netbet" },
    });

    await waitFor(() =>
      expect(onChange).toHaveBeenLastCalledWith({ text: "netbet", object: "clover" })
    );
  });

  it("debounces the text input instead of calling onChange on every keystroke", async () => {
    vi.useFakeTimers();
    try {
      vi.spyOn(api, "fetchObjects").mockResolvedValue([]);
      const onChange = vi.fn();

      render(<SearchFilters onChange={onChange} />);
      expect(onChange).toHaveBeenCalledTimes(1); // initial mount call, fires without a timer

      const input = screen.getByRole("textbox", { name: "Search images" });
      fireEvent.change(input, { target: { value: "n" } });
      fireEvent.change(input, { target: { value: "ne" } });
      fireEvent.change(input, { target: { value: "net" } });

      // Keystrokes alone must not have triggered a new call yet.
      expect(onChange).toHaveBeenCalledTimes(1);

      await act(async () => {
        await vi.advanceTimersByTimeAsync(300);
      });

      // Only one additional call, for the settled value — not one per keystroke.
      expect(onChange).toHaveBeenCalledTimes(2);
      expect(onChange).toHaveBeenLastCalledWith({ text: "net", object: undefined });
    } finally {
      vi.useRealTimers();
    }
  });

  it("reports the format and date-range facets from the More filters panel", async () => {
    vi.spyOn(api, "fetchObjects").mockResolvedValue([]);
    const onChange = vi.fn();

    render(<SearchFilters onChange={onChange} />);
    await waitFor(() => expect(onChange).toHaveBeenCalled());

    fireEvent.change(screen.getByRole("combobox", { name: "Filter by format" }), {
      target: { value: "png" },
    });
    fireEvent.change(screen.getByLabelText("Date field"), { target: { value: "mtime" } });
    fireEvent.change(screen.getByLabelText("From date"), { target: { value: "2024-01-01" } });

    await waitFor(() =>
      expect(onChange).toHaveBeenLastCalledWith({
        text: undefined,
        object: undefined,
        format: "png",
        dateField: "mtime",
        dateFrom: Date.parse("2024-01-01T00:00:00Z") / 1000,
      })
    );
  });

  it("keeps text search usable when optional filter lists fail to load", async () => {
    vi.spyOn(api, "fetchObjects").mockRejectedValue(new Error("offline"));

    render(<SearchFilters onChange={vi.fn()} />);

    await waitFor(() => expect(api.fetchObjects).toHaveBeenCalled());
    expect(screen.getByRole("textbox", { name: "Search images" })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Filter by object" })).toBeInTheDocument();
  });
});
