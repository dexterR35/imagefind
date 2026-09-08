import { cleanup, render, screen, fireEvent, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "./api";
import { ReindexProgress } from "./ReindexProgress";
import type { ReindexStatus } from "./api";

function status(over: Partial<ReindexStatus> = {}): ReindexStatus {
  return {
    job_id: "job1", processed: 40, total: 200, failed: 0,
    done: false, error: null, cancelled: false, elapsed_seconds: 10,
    ...over,
  };
}

describe("ReindexProgress", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    window.sessionStorage.clear();
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    cleanup();
  });

  it("reattaches to a run already in flight, so closing the tab does not lose it", async () => {
    vi.spyOn(api, "fetchCurrentReindex").mockResolvedValue(status());
    vi.spyOn(api, "fetchReindexStatus").mockResolvedValue(status());
    const onRunningChange = vi.fn();

    render(<ReindexProgress onRunningChange={onRunningChange} />);

    expect(await screen.findByText("Indexing images")).toBeInTheDocument();
    expect(screen.getByText(/40 \/ 200 images/)).toBeInTheDocument();
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "20");
    await waitFor(() => expect(onRunningChange).toHaveBeenCalledWith(true));
  });

  it("polls to completion, then reports it", async () => {
    vi.spyOn(api, "fetchCurrentReindex").mockResolvedValue(status());
    vi.spyOn(api, "fetchReindexStatus")
      .mockResolvedValueOnce(status({ processed: 120 }))
      .mockResolvedValue(status({ processed: 200, done: true }));
    const onComplete = vi.fn();

    render(<ReindexProgress onComplete={onComplete} />);
    await screen.findByText("Indexing images");

    await vi.advanceTimersByTimeAsync(1200);
    await waitFor(() => expect(screen.getByText("Indexing complete")).toBeInTheDocument());
    expect(onComplete).toHaveBeenCalled();
  });

  it("cancels the run from Stop", async () => {
    vi.spyOn(api, "fetchCurrentReindex").mockResolvedValue(status());
    vi.spyOn(api, "fetchReindexStatus").mockResolvedValue(status());
    const cancelSpy = vi.spyOn(api, "cancelReindex").mockResolvedValue();

    render(<ReindexProgress />);
    fireEvent.click(await screen.findByText("Stop"));

    await waitFor(() => expect(cancelSpy).toHaveBeenCalledWith("job1"));
  });

  it("hides the bar without cancelling the run", async () => {
    vi.spyOn(api, "fetchCurrentReindex").mockResolvedValue(status());
    vi.spyOn(api, "fetchReindexStatus").mockResolvedValue(status());
    const cancelSpy = vi.spyOn(api, "cancelReindex").mockResolvedValue();

    render(<ReindexProgress />);
    fireEvent.click(await screen.findByLabelText("Hide indexing progress"));

    await waitFor(() => expect(screen.queryByText("Indexing images")).not.toBeInTheDocument());
    expect(cancelSpy).not.toHaveBeenCalled();
    // Re-attaching must not bring the dismissed job back.
    await vi.advanceTimersByTimeAsync(5000);
    expect(screen.queryByText("Indexing images")).not.toBeInTheDocument();
  });

  it("shows an indeterminate bar while the scan has no total yet", async () => {
    vi.spyOn(api, "fetchCurrentReindex").mockResolvedValue(status({ processed: 0, total: 0 }));
    vi.spyOn(api, "fetchReindexStatus").mockResolvedValue(status({ processed: 0, total: 0 }));

    render(<ReindexProgress />);

    expect(await screen.findByText(/Scanning folders/)).toBeInTheDocument();
    expect(screen.getByRole("progressbar")).not.toHaveAttribute("aria-valuenow");
  });

  it("reports a stopped run as kept work, not a failure", async () => {
    vi.spyOn(api, "fetchCurrentReindex").mockResolvedValue(
      status({ processed: 90, done: true, cancelled: true }),
    );
    vi.spyOn(api, "fetchReindexStatus").mockResolvedValue(
      status({ processed: 90, done: true, cancelled: true }),
    );

    render(<ReindexProgress />);

    expect(await screen.findByText("Indexing stopped")).toBeInTheDocument();
    expect(screen.getByText(/90 image\(s\) already done stay indexed/)).toBeInTheDocument();
  });

  it("stays out of the way through the tunnel, where indexing is not allowed", async () => {
    const currentSpy = vi.spyOn(api, "fetchCurrentReindex");

    render(<ReindexProgress enabled={false} />);

    await vi.advanceTimersByTimeAsync(5000);
    expect(currentSpy).not.toHaveBeenCalled();
  });
});
