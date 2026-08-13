import { useEffect, useRef, useState } from "react";
import { fetchReindexStatus, startReindex, type ReindexStatus } from "./api";

interface Props {
  onComplete: () => void;
}

export function ReindexButton({ onComplete }: Props) {
  const [status, setStatus] = useState<ReindexStatus | null>(null);
  const [running, setRunning] = useState(false);
  const pollRef = useRef<number | null>(null);

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
    setRunning(true);
    setStatus(null);
    let jobId: string;
    try {
      jobId = await startReindex();
    } catch {
      // startReindex itself failed, so no polling ever starts — without this
      // catch the button would stay disabled ("Reindexing...") forever.
      setStatus({ processed: 0, total: 0, done: true, error: "Failed to start reindex." });
      setRunning(false);
      return;
    }

    pollRef.current = window.setInterval(async () => {
      try {
        const s = await fetchReindexStatus(jobId);
        setStatus(s);
        if (s.done) {
          stopPolling();
          setRunning(false);
          onComplete();
        }
      } catch {
        stopPolling();
        setRunning(false);
        setStatus({ processed: 0, total: 0, done: true, error: "Lost connection while checking reindex status." });
      }
    }, 500);
  }

  return (
    <div className="reindex">
      <button type="button" onClick={handleClick} disabled={running}>
        {running ? "Reindexing..." : "Reindex"}
      </button>
      {status && !status.done && (
        <span>
          {status.processed} / {status.total}
        </span>
      )}
      {status?.error && <span className="reindex-error">{status.error}</span>}
    </div>
  );
}
