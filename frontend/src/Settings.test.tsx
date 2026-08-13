import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "./api";
import { Settings } from "./Settings";

const sampleSettings: api.Settings = {
  yolo_confidence: 0.4,
  owl_confidence: 0.15,
  text_similarity_threshold: 0.2,
  color_clusters: 4,
  color_min_share: 0.08,
  vocabulary: ["clover", "horseshoe"],
};

describe("Settings", () => {
  beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: true }));
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    cleanup();
  });

  it("loads current settings when opened and pre-fills the vocabulary field", async () => {
    vi.spyOn(api, "fetchSettings").mockResolvedValue(sampleSettings);

    render(<Settings onReindexComplete={vi.fn()} />);
    fireEvent.click(screen.getByText("Settings"));

    await waitFor(() => expect(screen.getByDisplayValue("0.15")).toBeInTheDocument());
    expect(screen.getByDisplayValue("clover, horseshoe")).toBeInTheDocument();
  });

  it("saves edited settings, triggers a forced reindex, and polls to completion", async () => {
    vi.spyOn(api, "fetchSettings").mockResolvedValue(sampleSettings);
    const updateSpy = vi.spyOn(api, "updateSettings").mockResolvedValue({
      ...sampleSettings,
      owl_confidence: 0.05,
      vocabulary: ["clover", "diamond"],
    });
    const reindexSpy = vi.spyOn(api, "startReindex").mockResolvedValue("job1");
    vi.spyOn(api, "fetchReindexStatus").mockResolvedValue({ processed: 1, total: 1, done: true, error: null });
    const onReindexComplete = vi.fn();

    render(<Settings onReindexComplete={onReindexComplete} />);
    fireEvent.click(screen.getByText("Settings"));
    await waitFor(() => expect(screen.getByDisplayValue("0.15")).toBeInTheDocument());

    fireEvent.change(screen.getByDisplayValue("0.15"), { target: { value: "0.05" } });
    fireEvent.change(screen.getByDisplayValue("clover, horseshoe"), {
      target: { value: "clover, diamond" },
    });
    fireEvent.click(screen.getByText("Save & Reindex"));

    await waitFor(() =>
      expect(updateSpy).toHaveBeenCalledWith(
        expect.objectContaining({ owl_confidence: 0.05, vocabulary: ["clover", "diamond"] })
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
});
