import { render, screen, fireEvent } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "./api";
import { ReindexButton } from "./ReindexButton";

describe("ReindexButton", () => {
  beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: true }));
  afterEach(() => vi.useRealTimers());

  it("polls status until done then calls onComplete", async () => {
    vi.spyOn(api, "startReindex").mockResolvedValue("job1");
    const statusSpy = vi
      .spyOn(api, "fetchReindexStatus")
      .mockResolvedValueOnce({ processed: 1, total: 2, done: false, error: null })
      .mockResolvedValueOnce({ processed: 2, total: 2, done: true, error: null });
    const onComplete = vi.fn();

    render(<ReindexButton onComplete={onComplete} />);
    fireEvent.click(screen.getByText("Reindex"));

    await vi.advanceTimersByTimeAsync(500);
    await vi.advanceTimersByTimeAsync(500);

    expect(statusSpy).toHaveBeenCalledTimes(2);
    expect(onComplete).toHaveBeenCalled();
  });
});
