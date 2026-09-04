import { useEffect, useState } from "react";
import { fetchDuplicates, type ImageResult } from "./api";

interface Props {
  onOpen: (image: ImageResult) => void;
}

function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return "";
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

export function Duplicates({ onOpen }: Props) {
  const [open, setOpen] = useState(false);
  const [groups, setGroups] = useState<ImageResult[][] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    let active = true;
    setGroups(null);
    setError(null);
    fetchDuplicates()
      .then((next) => {
        if (active) setGroups(next);
      })
      .catch(() => {
        if (active) setError("Could not scan for duplicates.");
      });
    return () => {
      active = false;
    };
  }, [open]);

  return (
    <div className="duplicates">
      <button
        type="button"
        className="icon-button duplicates-toggle"
        aria-label={open ? "Close duplicates" : "Find duplicates"}
        aria-expanded={open}
        title="Find duplicates"
        onClick={() => setOpen((value) => !value)}
      >
        <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
          <path fill="currentColor" d="M9 3h9a2 2 0 0 1 2 2v9h-2V5H9V3Zm-4 4h9a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V9a2 2 0 0 1 2-2Zm0 2v9h9V9H5Z" />
        </svg>
      </button>
      {open && (
        <div className="duplicates-panel">
          {error && <span className="reindex-error" role="alert">{error}</span>}
          {!error && !groups && <span>Scanning…</span>}
          {groups && groups.length === 0 && (
            <span className="duplicates-empty">No near-duplicates found.</span>
          )}
          {groups && groups.length > 0 && (
            <p className="duplicates-count">
              {groups.length} group{groups.length === 1 ? "" : "s"} of near-identical images
            </p>
          )}
          {groups?.map((group, index) => (
            <div key={group.map((img) => img.id).join("-") || index} className="duplicates-group">
              <span className="duplicates-group-head">{group.length} copies</span>
              <div className="duplicates-strip">
                {group.map((img) => {
                  const filename = img.path.split(/[\\/]/).pop() ?? img.path;
                  return (
                    <button
                      key={img.id}
                      type="button"
                      className="duplicates-item"
                      title={img.path}
                      onClick={() => onOpen(img)}
                    >
                      <img src={img.thumbnail_url} alt={filename} loading="lazy" />
                      <span className="duplicates-item-name">{filename}</span>
                      <span className="duplicates-item-size">{formatBytes(img.size)}</span>
                    </button>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
