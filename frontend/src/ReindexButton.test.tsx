import { cleanup, render, screen, fireEvent, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "./api";
import { ReindexButton } from "./ReindexButton";

describe("ReindexButton", () => {
  beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: true }));
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    cleanup();
  });

  it("polls status until done then calls onComplete", async () => {
    vi.spyOn(api, "startReindex").mockResolvedValue("job1");
    const statusSpy = vi
      .spyOn(api, "fetchReindexStatus")
      .mockResolvedValueOnce({ processed: 1, total: 2, failed: 0, done: false, error: null, cancelled: false })
      .mockResolvedValueOnce({ processed: 2, total: 2, failed: 0, done: true, error: null, cancelled: false });
    const onComplete = vi.fn();

    render(<ReindexButton onComplete={onComplete} />);
    fireEvent.click(screen.getByText("Reindex"));

    await vi.advanceTimersByTimeAsync(500);
    await vi.advanceTimersByTimeAsync(500);

    expect(statusSpy).toHaveBeenCalledTimes(2);
    expect(onComplete).toHaveBeenCalled();
  });

  it("calls the latest onComplete even if a new one was passed before the job finished", async () => {
    // Regression test: onComplete used to be captured once at click time,
    // so if the parent re-rendered with a new onComplete (e.g. because
    // search filters changed) while a reindex was still in progress, the
    // stale one would fire instead.
    vi.spyOn(api, "startReindex").mockResolvedValue("job1");
    vi.spyOn(api, "fetchReindexStatus").mockResolvedValue({
      processed: 1, total: 1, failed: 0, done: true, error: null, cancelled: false,
    });
    const staleOnComplete = vi.fn();
    const freshOnComplete = vi.fn();

    const { rerender } = render(<ReindexButton onComplete={staleOnComplete} />);
    fireEvent.click(screen.getByText("Reindex"));
    rerender(<ReindexButton onComplete={freshOnComplete} />);

    await vi.advanceTimersByTimeAsync(500);

    expect(freshOnComplete).toHaveBeenCalled();
    expect(staleOnComplete).not.toHaveBeenCalled();
  });

  it("shows a warning when some images failed to index, without treating it as an error", async () => {
    vi.spyOn(api, "startReindex").mockResolvedValue("job1");
    vi.spyOn(api, "fetchReindexStatus").mockResolvedValue({
      processed: 10, total: 10, failed: 3, done: true, error: null, cancelled: false,
    });
    const onComplete = vi.fn();

    render(<ReindexButton onComplete={onComplete} />);
    fireEvent.click(screen.getByText("Reindex"));
    await vi.advanceTimersByTimeAsync(500);

    await waitFor(() => expect(screen.getByText(/3 image\(s\) failed to index/i)).toBeInTheDocument());
    expect(onComplete).toHaveBeenCalled();
  });

  it("re-enables the button and shows an error if startReindex rejects", async () => {
    vi.spyOn(api, "startReindex").mockRejectedValue(new Error("network down"));
    const onComplete = vi.fn();

    render(<ReindexButton onComplete={onComplete} />);
    fireEvent.click(screen.getByText("Reindex"));
    await vi.advanceTimersByTimeAsync(0);

    await waitFor(() => expect(screen.getByText("Reindex")).not.toBeDisabled());
    expect(screen.getByText(/failed to start reindex/i)).toBeInTheDocument();
    expect(onComplete).not.toHaveBeenCalled();
  });

  it("stops polling and reports partial progress when the job is cancelled", async () => {
    vi.spyOn(api, "startReindex").mockResolvedValue("job1");
    vi.spyOn(api, "cancelReindex").mockResolvedValue(undefined);
    const statusSpy = vi
      .spyOn(api, "fetchReindexStatus")
      .mockResolvedValueOnce({ processed: 1, total: 10, failed: 0, done: false, error: null, cancelled: false })
      .mockResolvedValueOnce({ processed: 1, total: 10, failed: 0, done: true, error: null, cancelled: true });
    const onComplete = vi.fn();

    render(<ReindexButton onComplete={onComplete} />);
    fireEvent.click(screen.getByText("Reindex"));
    await vi.advanceTimersByTimeAsync(500);

    fireEvent.click(screen.getByText("Stop"));
    await vi.advanceTimersByTimeAsync(0);
    expect(api.cancelReindex).toHaveBeenCalledWith("job1");

    await vi.advanceTimersByTimeAsync(500);

    expect(statusSpy).toHaveBeenCalledTimes(2);
    await waitFor(() =>
      expect(screen.getByText(/reindex stopped.*kept 1 already-processed image/i)).toBeInTheDocument(),
    );
    expect(screen.queryByText("Stop")).not.toBeInTheDocument();
    expect(onComplete).toHaveBeenCalled();
  });

  it("re-enables the button and shows an error if a status poll fails", async () => {
    vi.spyOn(api, "startReindex").mockResolvedValue("job1");
    vi.spyOn(api, "fetchReindexStatus").mockRejectedValue(new Error("lost connection"));
    const onComplete = vi.fn();

    render(<ReindexButton onComplete={onComplete} />);
    fireEvent.click(screen.getByText("Reindex"));
    await vi.advanceTimersByTimeAsync(0);
    await vi.advanceTimersByTimeAsync(500);
    await vi.advanceTimersByTimeAsync(0);

    await waitFor(() => expect(screen.getByText("Reindex")).not.toBeDisabled());
    expect(screen.getByText(/lost connection/i)).toBeInTheDocument();
    expect(onComplete).not.toHaveBeenCalled();
  });
});
