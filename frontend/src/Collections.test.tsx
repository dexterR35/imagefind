import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import * as api from "./api";
import { Collections } from "./Collections";

const one: api.Collection = { id: "c1", name: "Campaign", created_at: 0, count: 3 };

describe("Collections", () => {
  it("creates, renames and deletes collections and refreshes the list", async () => {
    const onChanged = vi.fn();
    const create = vi.spyOn(api, "createCollection").mockResolvedValue({
      id: "c2", name: "New", created_at: 0, count: 0,
    });
    const rename = vi.spyOn(api, "renameCollection").mockResolvedValue();
    const remove = vi.spyOn(api, "deleteCollection").mockResolvedValue();
    vi.spyOn(window, "confirm").mockReturnValue(true);

    render(<Collections collections={[one]} onChanged={onChanged} />);
    fireEvent.click(screen.getByRole("button", { name: "Open collections" }));

    fireEvent.change(screen.getByLabelText("New collection name"), { target: { value: "New" } });
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    await waitFor(() => expect(create).toHaveBeenCalledWith("New"));
    expect(onChanged).toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Rename Campaign" }));
    fireEvent.change(screen.getByLabelText("Rename Campaign"), { target: { value: "Campaign 2" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(rename).toHaveBeenCalledWith("c1", "Campaign 2"));

    fireEvent.click(screen.getByRole("button", { name: "Delete Campaign" }));
    await waitFor(() => expect(remove).toHaveBeenCalledWith("c1"));
  });

  it("surfaces a duplicate-name error", async () => {
    vi.spyOn(api, "createCollection").mockRejectedValue(new Error("a collection named 'X' already exists"));

    render(<Collections collections={[]} onChanged={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Open collections" }));
    fireEvent.change(screen.getByLabelText("New collection name"), { target: { value: "X" } });
    fireEvent.click(screen.getByRole("button", { name: "Add" }));

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/already exists/i));
  });
});
