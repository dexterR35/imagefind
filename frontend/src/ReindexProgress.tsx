import { useCallback, useEffect, useRef, useState } from "react";
import {
  cancelReindex,
  fetchCurrentReindex,
  fetchReindexStatus,
  streamReindexStatus,
  type ReindexStatus,
} from "./api";

interface Props {
  // Off through the tunnel: the reindex endpoints are local-admin only.
  enabled?: boolean;
  // Bumped by the parent when a reindex is started from the settings panel, so
  // the bar picks it up at once instead of waiting for the next idle poll.
  startToken?: number;
  onComplete?: () => void;
  onRunningChange?: (running: boolean) => void;
}

// How often to ask whether a run has appeared (started here, in another tab, or
// before this tab was even opened). Cheap: it reads in-memory job state.
const IDLE_POLL_MS = 4000;
// Fallback cadence while attached, when EventSource is unavailable.
const ATTACHED_POLL_MS = 500;
const AUTO_HIDE_MS = 8000;
const DISMISS_KEY = "imagefind.reindex.dismissed";
const SYNTHETIC_ID = "local";

function readDismissed(): string | null {
  try {
    return window.sessionStorage.getItem(DISMISS_KEY);
  } catch {
    // Private mode / storage disabled. Never dismissed is the safe default.
    return null;
  }
}

function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "";
  const total = Math.round(seconds);
  if (total < 60) return `${total}s`;
  const minutes = Math.floor(total / 60);
  if (minutes < 60) return `${minutes}m ${total % 60}s`;
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

export function ReindexProgress({ enabled = true, startToken = 0, onComplete, onRunningChange }: Props) {
  const [status, setStatus] = useState<ReindexStatus | null>(null);
  const [stopping, setStopping] = useState(false);
  const [dismissed, setDismissed] = useState<string | null>(readDismissed);
  // The job this bar is subscribed to, and how to unsubscribe from it.
  const attachedRef = useRef<string | null>(null);
  const unsubscribeRef = useRef<(() => void) | null>(null);
  // Callbacks can change identity between renders; the long-lived subscription
  // has to call whichever one is current when it actually fires.
  const onCompleteRef = useRef(onComplete);
  const onRunningChangeRef = useRef(onRunningChange);
  useEffect(() => {
    onCompleteRef.current = onComplete;
    onRunningChangeRef.current = onRunningChange;
  }, [onComplete, onRunningChange]);

  const unsubscribe = useCallback(() => {
    if (unsubscribeRef.current) {
      unsubscribeRef.current();
      unsubscribeRef.current = null;
    }
    attachedRef.current = null;
  }, []);

  const attach = useCallback((jobId: string, seed?: ReindexStatus) => {
    if (attachedRef.current === jobId) return;
    unsubscribe();
    attachedRef.current = jobId;
    if (seed) setStatus(seed);
    onRunningChangeRef.current?.(true);

    const settle = () => {
      unsubscribe();
      setStopping(false);
      onRunningChangeRef.current?.(false);
    };
    const onProgress = (next: ReindexStatus) => {
      setStatus({ ...next, job_id: next.job_id ?? jobId });
      if (next.done) {
        settle();
        onCompleteRef.current?.();
      }
    };
    const onLost = () => {
      settle();
      setStatus((prev) =>
        prev ? { ...prev, done: true, error: "Lost the connection to the indexer. It may still be running." } : prev,
      );
    };

    const stopStream = streamReindexStatus(jobId, onProgress, onLost);
    if (stopStream) {
      unsubscribeRef.current = stopStream;
    } else {
      const timer = window.setInterval(async () => {
        try {
          onProgress(await fetchReindexStatus(jobId));
        } catch {
          onLost();
        }
      }, ATTACHED_POLL_MS);
      unsubscribeRef.current = () => window.clearInterval(timer);
    }
  }, [unsubscribe]);

  // Look for a run to follow, forever, until one is found — then again once it
  // ends. This is what makes a reopened tab reattach to work already in flight.
  useEffect(() => {
    if (!enabled) return undefined;
    let stopped = false;
    let timer: number | null = null;

    async function look() {
      if (stopped) return;
      if (attachedRef.current === null) {
        try {
          const job = await fetchCurrentReindex();
          if (!stopped && job?.job_id) attach(job.job_id, job);
        } catch {
          // Server down or not admin here; just try again on the next tick.
        }
      }
      if (!stopped) timer = window.setTimeout(look, IDLE_POLL_MS);
    }

    look();
    return () => {
      stopped = true;
      if (timer !== null) window.clearTimeout(timer);
    };
  }, [enabled, startToken, attach]);

  useEffect(() => unsubscribe, [unsubscribe]);

  // A clean finish doesn't need dismissing by hand.
  const cleanFinish = !!status?.done && !status.cancelled && !status.error && status.failed === 0;
  useEffect(() => {
    if (!cleanFinish) return undefined;
    const jobId = status?.job_id ?? SYNTHETIC_ID;
    const timer = window.setTimeout(() => hide(jobId), AUTO_HIDE_MS);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cleanFinish, status?.job_id]);

  function hide(jobId: string) {
    setDismissed(jobId);
    try {
      window.sessionStorage.setItem(DISMISS_KEY, jobId);
    } catch {
      // Hiding for this render is enough; it just won't survive a reload.
    }
  }

  async function handleStop() {
    const jobId = attachedRef.current;
    if (!jobId) return;
    setStopping(true);
    try {
      await cancelReindex(jobId);
    } catch {
      setStopping(false);
    }
  }

  if (!enabled || !status) return null;
  const jobId = status.job_id ?? SYNTHETIC_ID;
  if (dismissed === jobId) return null;

  const running = !status.done;
  const scanning = running && status.total === 0;
  const percent = status.total > 0 ? Math.min(100, Math.round((status.processed / status.total) * 100)) : 0;
  const elapsed = status.elapsed_seconds ?? 0;
  // Only worth showing once enough of the run has gone by to mean anything.
  const remaining =
    running && status.total > 0 && status.processed > 10 && elapsed > 2
      ? (elapsed / status.processed) * (status.total - status.processed)
      : null;

  let title = "Indexing images";
  let tone = "is-running";
  if (status.done) {
    if (status.error) { title = "Indexing failed"; tone = "is-error"; }
    else if (status.cancelled) { title = "Indexing stopped"; tone = "is-stopped"; }
    else { title = "Indexing complete"; tone = "is-done"; }
  }

  return (
    <section className={`reindex-bar ${tone}`} aria-label="Indexing progress">
      <div className="reindex-bar-head">
        <span className="reindex-bar-title">{title}</span>
        <span className="reindex-bar-meta" role="status" aria-live="polite">
          {scanning
            ? "Scanning folders…"
            : `${status.processed.toLocaleString()} / ${status.total.toLocaleString()} images`}
          {!scanning && status.total > 0 && <> · {percent}%</>}
          {remaining !== null && <> · ~{formatDuration(remaining)} left</>}
          {status.done && elapsed > 0 && <> · took {formatDuration(elapsed)}</>}
        </span>
        {running && (
          <button
            type="button"
            className="btn-ghost reindex-stop"
            onClick={handleStop}
            disabled={stopping}
          >
            {stopping ? "Stopping…" : "Stop"}
          </button>
        )}
        <button
          type="button"
          className="icon-button reindex-hide"
          aria-label="Hide indexing progress"
          title={running ? "Hide — indexing keeps running" : "Hide"}
          onClick={() => hide(jobId)}
        >
          ×
        </button>
      </div>
      <div
        className={`reindex-track${scanning ? " is-indeterminate" : ""}`}
        role="progressbar"
        aria-label="Images indexed"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={scanning ? undefined : percent}
      >
        <div className="reindex-fill" style={scanning ? undefined : { width: `${percent}%` }} />
      </div>
      <p className="reindex-note">
        {status.error
          ? status.error
          : status.cancelled
            ? `Stopped early — the ${status.processed.toLocaleString()} image(s) already done stay indexed.`
            : running
              ? "Runs on the server — you can close this tab and it will keep going. Only Stop cancels it."
              : "Index up to date."}
        {status.failed > 0 && (
          <span className="reindex-error"> {status.failed.toLocaleString()} image(s) failed — check the server logs.</span>
        )}
      </p>
    </section>
  );
}
