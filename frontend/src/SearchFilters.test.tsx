import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import * as api from "./api";
import { SearchFilters } from "./SearchFilters";

describe("SearchFilters", () => {
  it("loads colors/objects and reports combined filter changes", async () => {
    vi.spyOn(api, "fetchColors").mockResolvedValue(["green", "blue"]);
    vi.spyOn(api, "fetchObjects").mockResolvedValue(["clover", "person"]);
    const onChange = vi.fn();

    render(<SearchFilters onChange={onChange} />);

    await waitFor(() => expect(screen.getByLabelText("green")).toBeInTheDocument());

    fireEvent.click(screen.getByLabelText("green"));
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "clover" } });
    fireEvent.change(screen.getByPlaceholderText("Search text or meaning..."), {
      target: { value: "netbet" },
    });

    await waitFor(() =>
      expect(onChange).toHaveBeenLastCalledWith({ text: "netbet", color: "green", object: "clover" })
    );
  });
});
