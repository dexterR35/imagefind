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
    return () => {
      if (pollRef.current !== null) {
        window.clearInterval(pollRef.current);
      }
    };
  }, []);

  async function handleClick() {
    setRunning(true);
    const jobId = await startReindex();
    pollRef.current = window.setInterval(async () => {
      const s = await fetchReindexStatus(jobId);
      setStatus(s);
      if (s.done) {
        window.clearInterval(pollRef.current!);
        setRunning(false);
        onComplete();
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
    </div>
  );
}
