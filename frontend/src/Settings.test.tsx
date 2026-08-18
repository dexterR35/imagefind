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
    // Most tests aren't exercising the model-install feature, so default to
    // "already installed" (no install button) unless a test overrides this.
    vi.spyOn(api, "fetchModelStatus").mockResolvedValue({ installed: true });
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    cleanup();
  });

  it("loads current settings when opened and pre-fills the custom tags field", async () => {
    vi.spyOn(api, "fetchSettings").mockResolvedValue(sampleSettings);

    render(<Settings onReindexComplete={vi.fn()} />);
    fireEvent.click(screen.getByText("Settings"));

    await waitFor(() => expect(screen.getByDisplayValue("0.15")).toBeInTheDocument());
    expect(screen.getByDisplayValue("zeus, lightning")).toBeInTheDocument();
    expect(screen.getByDisplayValue("/photos")).toBeInTheDocument();
  });

  it("shows a connection error when settings fail to load", async () => {
    vi.spyOn(api, "fetchSettings").mockRejectedValue(new Error("offline"));

    render(<Settings onReindexComplete={vi.fn()} />);
    fireEvent.click(screen.getByText("Settings"));

    expect(await screen.findByRole("alert")).toHaveTextContent("Could not load settings");
  });

  it("includes the edited image folder path when saving", async () => {
    vi.spyOn(api, "fetchSettings").mockResolvedValue(sampleSettings);
    const updateSpy = vi.spyOn(api, "updateSettings").mockResolvedValue({
      ...sampleSettings,
      images_dir: "/other-photos",
    });
    vi.spyOn(api, "startReindex").mockResolvedValue("job1");
    vi.spyOn(api, "fetchReindexStatus").mockResolvedValue({ processed: 1, total: 1, failed: 0, done: true, error: null, cancelled: false });

    render(<Settings onReindexComplete={vi.fn()} />);
    fireEvent.click(screen.getByText("Settings"));
    await waitFor(() => expect(screen.getByDisplayValue("/photos")).toBeInTheDocument());

    fireEvent.change(screen.getByDisplayValue("/photos"), { target: { value: "/other-photos" } });
    fireEvent.click(screen.getByText("Save & Reindex"));

    await waitFor(() =>
      expect(updateSpy).toHaveBeenCalledWith(expect.objectContaining({ images_dir: "/other-photos" }))
    );
  });

  it("saves edited settings, triggers a forced reindex, and polls to completion", async () => {
    vi.spyOn(api, "fetchSettings").mockResolvedValue(sampleSettings);
    const updateSpy = vi.spyOn(api, "updateSettings").mockResolvedValue({
      ...sampleSettings,
      ram_confidence: 0.05,
      ram_custom_tags: ["zeus", "statue"],
    });
    const reindexSpy = vi.spyOn(api, "startReindex").mockResolvedValue("job1");
    vi.spyOn(api, "fetchReindexStatus").mockResolvedValue({ processed: 1, total: 1, failed: 0, done: true, error: null, cancelled: false });
    const onReindexComplete = vi.fn();

    render(<Settings onReindexComplete={onReindexComplete} />);
    fireEvent.click(screen.getByText("Settings"));
    await waitFor(() => expect(screen.getByDisplayValue("0.15")).toBeInTheDocument());

    fireEvent.change(screen.getByDisplayValue("0.15"), { target: { value: "0.05" } });
    fireEvent.change(screen.getByDisplayValue("zeus, lightning"), {
      target: { value: "zeus, statue" },
    });
    fireEvent.click(screen.getByText("Save & Reindex"));

    await waitFor(() =>
      expect(updateSpy).toHaveBeenCalledWith(
        expect.objectContaining({ ram_confidence: 0.05, ram_custom_tags: ["zeus", "statue"] })
      )
    );
    // startReindex must be called with force=true — settings changes need a
    // full re-scan, not the default skip-unchanged reindex.
    expect(reindexSpy).toHaveBeenCalledWith(true);

    await vi.advanceTimersByTimeAsync(500);
    expect(onReindexComplete).toHaveBeenCalled();
  });

  it("shows an error and stops without reindexing if saving settings fails", async () => {
    vi.spyOn(api, "fetchSettings").mockResolvedValue(sampleSettings);
    vi.spyOn(api, "updateSettings").mockRejectedValue(new Error("network down"));
    const reindexSpy = vi.spyOn(api, "startReindex");

    render(<Settings onReindexComplete={vi.fn()} />);
    fireEvent.click(screen.getByText("Settings"));
    await waitFor(() => expect(screen.getByDisplayValue("0.15")).toBeInTheDocument());

    fireEvent.click(screen.getByText("Save & Reindex"));
    await vi.advanceTimersByTimeAsync(0);

    await waitFor(() => expect(screen.getByText(/failed to save settings/i)).toBeInTheDocument());
    expect(reindexSpy).not.toHaveBeenCalled();
    expect(screen.getByText("Save & Reindex")).not.toBeDisabled();
  });

  it("reports that settings were saved even if starting the reindex then fails", async () => {
    // Regression test: a single try/catch around both calls used to report
    // a generic "failed to save settings or start reindex" even when the
    // save half had actually already succeeded (e.g. a 409 because another
    // reindex was already running).
    vi.spyOn(api, "fetchSettings").mockResolvedValue(sampleSettings);
    const updateSpy = vi.spyOn(api, "updateSettings").mockResolvedValue(sampleSettings);
    vi.spyOn(api, "startReindex").mockRejectedValue(new Error("409 already running"));

    render(<Settings onReindexComplete={vi.fn()} />);
    fireEvent.click(screen.getByText("Settings"));
    await waitFor(() => expect(screen.getByDisplayValue("0.15")).toBeInTheDocument());

    fireEvent.click(screen.getByText("Save & Reindex"));
    await vi.advanceTimersByTimeAsync(0);

    await waitFor(() => expect(updateSpy).toHaveBeenCalled());
    await waitFor(() =>
      expect(screen.getByText(/settings saved, but failed to start reindex/i)).toBeInTheDocument()
    );
    expect(screen.getByText("Save & Reindex")).not.toBeDisabled();
  });

  it("does not show an install button when the model is already installed", async () => {
    vi.spyOn(api, "fetchSettings").mockResolvedValue(sampleSettings);
    vi.spyOn(api, "fetchModelStatus").mockResolvedValue({ installed: true });

    render(<Settings onReindexComplete={vi.fn()} />);
    fireEvent.click(screen.getByText("Settings"));

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
    fireEvent.click(screen.getByText("Settings"));

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

  it("shows an error if the model download fails", async () => {
    vi.spyOn(api, "fetchSettings").mockResolvedValue(sampleSettings);
    vi.spyOn(api, "fetchModelStatus").mockResolvedValue({ installed: false });
    vi.spyOn(api, "startModelDownload").mockResolvedValue("model-job1");
    vi.spyOn(api, "fetchModelDownloadStatus").mockResolvedValue({
      downloaded_bytes: 0, total_bytes: 0, done: true, cancelled: false,
      error: "Failed to download RAM++ checkpoint: connection reset",
    });

    render(<Settings onReindexComplete={vi.fn()} />);
    fireEvent.click(screen.getByText("Settings"));

    const installButton = await screen.findByText("Install RAM++ Model");
    fireEvent.click(installButton);

    await vi.advanceTimersByTimeAsync(500);
    await waitFor(() => expect(screen.getByText(/connection reset/i)).toBeInTheDocument());
    // The button is shown again (not stuck disabled) so the user can retry.
    expect(screen.getByText("Install RAM++ Model")).not.toBeDisabled();
  });
});
