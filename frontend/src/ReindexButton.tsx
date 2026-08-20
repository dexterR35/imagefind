import { useEffect, useRef, useState } from "react";
import { cancelReindex, fetchReindexStatus, startReindex, type ReindexStatus } from "./api";

interface Props {
  onComplete: () => void;
}

export function ReindexButton({ onComplete }: Props) {
  const [status, setStatus] = useState<ReindexStatus | null>(null);
  const [running, setRunning] = useState(false);
  const [stopping, setStopping] = useState(false);
  const pollRef = useRef<number | null>(null);
  const jobIdRef = useRef<string | null>(null);
  // handleClick's setInterval callback closes over whatever it captures at
  // click time and keeps using that for the whole poll — a plain `onComplete`
  // reference would go stale if the parent passes a new one (e.g. because
  // search filters changed) before the job finishes. Routing through a ref
  // that's kept current on every render means the interval always calls
  // whichever onComplete is newest when it actually fires.
  const onCompleteRef = useRef(onComplete);
  useEffect(() => {
    onCompleteRef.current = onComplete;
  }, [onComplete]);

  useEffect(() => {
    return () => stopPolling();
  }, []);

  function stopPolling() {
    if (pollRef.current !== null) {
      window.clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }

  async function handleClick() {
    if (!window.confirm("Reindex all image folders now? This can take a while.\n\nContinue?")) return;
    setRunning(true);
    setStopping(false);
    setStatus(null);
    let jobId: string;
    try {
      jobId = await startReindex();
    } catch {
      // startReindex itself failed, so no polling ever starts — without this
      // catch the button would stay disabled ("Reindexing...") forever.
      setStatus({ processed: 0, total: 0, failed: 0, done: true, error: "Failed to start reindex.", cancelled: false });
      setRunning(false);
      return;
    }
    jobIdRef.current = jobId;

    pollRef.current = window.setInterval(async () => {
      try {
        const s = await fetchReindexStatus(jobId);
        setStatus(s);
        if (s.done) {
          stopPolling();
          setRunning(false);
          setStopping(false);
          jobIdRef.current = null;
          onCompleteRef.current();
        }
      } catch {
        stopPolling();
        setRunning(false);
        setStopping(false);
        jobIdRef.current = null;
        setStatus({
          processed: 0, total: 0, failed: 0, done: true, cancelled: false,
          error: "Lost connection while checking reindex status.",
        });
      }
    }, 500);
  }

  async function handleStop() {
    if (!jobIdRef.current) return;
    setStopping(true);
    try {
      await cancelReindex(jobIdRef.current);
    } catch {
      setStopping(false);
    }
  }

  return (
    <div className="reindex">
      <button type="button" onClick={handleClick} disabled={running}>
        {running ? "Reindexing..." : "Reindex"}
      </button>
      {running && (
        <button type="button" onClick={handleStop} disabled={stopping}>
          {stopping ? "Stopping..." : "Stop"}
        </button>
      )}
      {status && !status.done && (
        <span>
          {status.processed} / {status.total}
        </span>
      )}
      {status?.done && status.cancelled && (
        <span>Reindex stopped — kept {status.processed} already-processed image(s).</span>
      )}
      {status?.done && status.failed > 0 && (
        <span className="reindex-error">{status.failed} image(s) failed to index — check server logs.</span>
      )}
      {status?.error && <span className="reindex-error">{status.error}</span>}
    </div>
  );
}
