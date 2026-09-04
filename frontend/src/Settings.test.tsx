import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "./api";
import { Settings } from "./Settings";

const sampleSettings: api.Settings = {
  ram_confidence: 0.15,
  ram_custom_tags: ["zeus", "lightning"],
  images_dir: "/photos",
};

describe("Settings", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.spyOn(window, "confirm").mockReturnValue(true);
    // Most tests aren't exercising the model-install feature, so default to
    // "already installed" (no install button) unless a test overrides this.
    vi.spyOn(api, "fetchModelStatus").mockResolvedValue({ installed: true });
    vi.spyOn(api, "fetchBackups").mockResolvedValue([]);
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    cleanup();
  });

  it("loads current settings when opened and pre-fills the custom tags field", async () => {
    vi.spyOn(api, "fetchSettings").mockResolvedValue(sampleSettings);

    render(<Settings onReindexComplete={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Open settings" }));

    await waitFor(() => expect(screen.getByDisplayValue("0.15")).toBeInTheDocument());
    expect(screen.getByDisplayValue("zeus, lightning")).toBeInTheDocument();
    expect(screen.getByDisplayValue("/photos")).toBeInTheDocument();
  });

  it("shows a connection error when settings fail to load", async () => {
    vi.spyOn(api, "fetchSettings").mockRejectedValue(new Error("offline"));

    render(<Settings onReindexComplete={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Open settings" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Could not load settings");
  });

  it("includes the edited image folder path when saving", async () => {
    vi.spyOn(api, "fetchSettings").mockResolvedValue(sampleSettings);
    const updateSpy = vi.spyOn(api, "updateSettings").mockResolvedValue({
      ...sampleSettings,
      images_dir: "/other-photos",
    });
    const reindexSpy = vi.spyOn(api, "startReindex");

    render(<Settings onReindexComplete={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Open settings" }));
    await waitFor(() => expect(screen.getByDisplayValue("/photos")).toBeInTheDocument());

    fireEvent.change(screen.getByDisplayValue("/photos"), { target: { value: "/other-photos" } });
    fireEvent.click(screen.getByText("Save"));

    expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining("switch the watched folder"));
    await waitFor(() =>
      expect(updateSpy).toHaveBeenCalledWith(expect.objectContaining({ images_dir: "/other-photos" }))
    );
    expect(reindexSpy).not.toHaveBeenCalled();
  });

  it("does not save a folder change when confirmation is declined", async () => {
    vi.spyOn(api, "fetchSettings").mockResolvedValue(sampleSettings);
    const updateSpy = vi.spyOn(api, "updateSettings");
    const reindexSpy = vi.spyOn(api, "startReindex");
    vi.mocked(window.confirm).mockReturnValueOnce(false);

    render(<Settings onReindexComplete={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Open settings" }));
    await waitFor(() => expect(screen.getByDisplayValue("/photos")).toBeInTheDocument());
    fireEvent.change(screen.getByDisplayValue("/photos"), { target: { value: "/other-photos" } });
    fireEvent.click(screen.getByText("Save"));

    expect(updateSpy).not.toHaveBeenCalled();
    expect(reindexSpy).not.toHaveBeenCalled();
  });

  it("saves edited settings without starting a reindex", async () => {
    vi.spyOn(api, "fetchSettings").mockResolvedValue(sampleSettings);
    const updateSpy = vi.spyOn(api, "updateSettings").mockResolvedValue({
      ...sampleSettings,
      ram_confidence: 0.05,
      ram_custom_tags: ["zeus", "statue"],
    });
    const reindexSpy = vi.spyOn(api, "startReindex");

    render(<Settings onReindexComplete={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Open settings" }));
    await waitFor(() => expect(screen.getByDisplayValue("0.15")).toBeInTheDocument());

    fireEvent.change(screen.getByDisplayValue("0.15"), { target: { value: "0.05" } });
    fireEvent.change(screen.getByDisplayValue("zeus, lightning"), {
      target: { value: "zeus, statue" },
    });
    fireEvent.click(screen.getByText("Save"));

    await waitFor(() =>
      expect(updateSpy).toHaveBeenCalledWith(
        expect.objectContaining({ ram_confidence: 0.05, ram_custom_tags: ["zeus", "statue"] })
      )
    );
    expect(reindexSpy).not.toHaveBeenCalled();
    expect(await screen.findByText("Settings saved.")).toBeInTheDocument();
  });

  it("shows an error and stops without reindexing if saving settings fails", async () => {
    vi.spyOn(api, "fetchSettings").mockResolvedValue(sampleSettings);
    vi.spyOn(api, "updateSettings").mockRejectedValue(new Error("network down"));
    const reindexSpy = vi.spyOn(api, "startReindex");

    render(<Settings onReindexComplete={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Open settings" }));
    await waitFor(() => expect(screen.getByDisplayValue("0.15")).toBeInTheDocument());

    fireEvent.click(screen.getByText("Save"));
    await vi.advanceTimersByTimeAsync(0);

    await waitFor(() => expect(screen.getByText(/failed to save settings/i)).toBeInTheDocument());
    expect(reindexSpy).not.toHaveBeenCalled();
    expect(screen.getByText("Save")).not.toBeDisabled();
  });

  it("starts a forced reindex independently and polls to completion", async () => {
    vi.spyOn(api, "fetchSettings").mockResolvedValue(sampleSettings);
    const updateSpy = vi.spyOn(api, "updateSettings");
    const reindexSpy = vi.spyOn(api, "startReindex").mockResolvedValue("job1");
    vi.spyOn(api, "fetchReindexStatus").mockResolvedValue({
      processed: 1, total: 1, failed: 0, done: true, error: null, cancelled: false,
    });
    const onReindexComplete = vi.fn();

    render(<Settings onReindexComplete={onReindexComplete} />);
    fireEvent.click(screen.getByRole("button", { name: "Open settings" }));
    await waitFor(() => expect(screen.getByDisplayValue("0.15")).toBeInTheDocument());

    fireEvent.click(screen.getByText("Reindex"));
    await waitFor(() => expect(reindexSpy).toHaveBeenCalledWith(true));
    expect(updateSpy).not.toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(500);
    await waitFor(() => expect(onReindexComplete).toHaveBeenCalled());
    expect(screen.getByText("Reindex")).not.toBeDisabled();
  });

  it("does not show an install button when the model is already installed", async () => {
    vi.spyOn(api, "fetchSettings").mockResolvedValue(sampleSettings);
    vi.spyOn(api, "fetchModelStatus").mockResolvedValue({ installed: true });

    render(<Settings onReindexComplete={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Open settings" }));

    await waitFor(() => expect(screen.getByDisplayValue("/photos")).toBeInTheDocument());
    expect(screen.queryByText("Install RAM++ Model")).not.toBeInTheDocument();
  });

  it("shows an install button when the model isn't installed, and installs it", async () => {
    vi.spyOn(api, "fetchSettings").mockResolvedValue(sampleSettings);
    vi.spyOn(api, "fetchModelStatus").mockResolvedValue({ installed: false });
    vi.spyOn(api, "startModelDownload").mockResolvedValue("model-job1");
    const statusSpy = vi
      .spyOn(api, "fetchModelDownloadStatus")
      .mockResolvedValueOnce({
        downloaded_bytes: 50 * 1024 * 1024, total_bytes: 100 * 1024 * 1024,
        done: false, error: null, cancelled: false,
      })
      .mockResolvedValue({
        downloaded_bytes: 100 * 1024 * 1024, total_bytes: 100 * 1024 * 1024,
        done: true, error: null, cancelled: false,
      });

    render(<Settings onReindexComplete={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Open settings" }));

    const installButton = await screen.findByText("Install RAM++ Model");
    fireEvent.click(installButton);

    await vi.advanceTimersByTimeAsync(500);
    await waitFor(() => expect(screen.getByText("50 / 100 MB")).toBeInTheDocument());

    await vi.advanceTimersByTimeAsync(500);
    await waitFor(() => expect(statusSpy).toHaveBeenCalledTimes(2));
    // Once the download completes, the button disappears since the model is
    // now considered installed.
    await waitFor(() => expect(screen.queryByText("Install RAM++ Model")).not.toBeInTheDocument());
  });

  it("creates an index backup and lists it", async () => {
    vi.spyOn(api, "fetchSettings").mockResolvedValue(sampleSettings);
    vi.mocked(api.fetchBackups).mockResolvedValue([
      { name: "index-20240101-000000.db", size: 2 * 1024 * 1024, created_at: 1 },
    ]);
    const create = vi.spyOn(api, "createBackup").mockResolvedValue({
      name: "index-20240102-120000.db", size: 3 * 1024 * 1024, created_at: 2,
    });

    render(<Settings onReindexComplete={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Open settings" }));

    await screen.findByText("index-20240101-000000.db");
    fireEvent.click(screen.getByRole("button", { name: "Back up now" }));

    await waitFor(() => expect(create).toHaveBeenCalled());
    expect(await screen.findByText("index-20240102-120000.db")).toBeInTheDocument();
    expect(screen.getByText("3.0 MB")).toBeInTheDocument();
  });

  it("shows an error if the model download fails", async () => {
    vi.spyOn(api, "fetchSettings").mockResolvedValue(sampleSettings);
    vi.spyOn(api, "fetchModelStatus").mockResolvedValue({ installed: false });
    vi.spyOn(api, "startModelDownload").mockResolvedValue("model-job1");
    vi.spyOn(api, "fetchModelDownloadStatus").mockResolvedValue({
      downloaded_bytes: 0, total_bytes: 0, done: true, cancelled: false,
      error: "Failed to download RAM++ checkpoint: connection reset",
    });

    render(<Settings onReindexComplete={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Open settings" }));

    const installButton = await screen.findByText("Install RAM++ Model");
    fireEvent.click(installButton);

    await vi.advanceTimersByTimeAsync(500);
    await waitFor(() => expect(screen.getByText(/connection reset/i)).toBeInTheDocument());
    // The button is shown again (not stuck disabled) so the user can retry.
    expect(screen.getByText("Install RAM++ Model")).not.toBeDisabled();
  });
});
