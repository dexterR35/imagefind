import { useEffect, useState } from "react";
import { fetchStats, type CatalogStats } from "./api";

function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value >= 10 || unit === 0 ? Math.round(value) : value.toFixed(1)} ${units[unit]}`;
}

function formatDate(timestamp: number | null): string {
  if (!timestamp) return "—";
  const date = new Date(timestamp * 1000);
  return Number.isNaN(date.getTime())
    ? "—"
    : date.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

function filename(path: string): string {
  return path.split(/[\\/]/).pop() ?? path;
}

function Bars({ rows }: { rows: { label: string; count: number }[] }) {
  const max = rows.reduce((peak, row) => Math.max(peak, row.count), 0) || 1;
  return (
    <div className="stats-bars">
      {rows.map((row) => (
        <div key={row.label} className="stats-bar-row">
          <span className="stats-bar-label">{row.label}</span>
          <span className="stats-bar-track">
            <span className="stats-bar-fill" style={{ width: `${(row.count / max) * 100}%` }} />
          </span>
          <span className="stats-bar-count">{row.count.toLocaleString()}</span>
        </div>
      ))}
    </div>
  );
}

export function Stats() {
  const [open, setOpen] = useState(false);
  const [stats, setStats] = useState<CatalogStats | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    let active = true;
    setError(null);
    fetchStats()
      .then((next) => {
        if (active) setStats(next);
      })
      .catch(() => {
        if (active) setError("Could not load stats. Check the backend connection.");
      });
    return () => {
      active = false;
    };
  }, [open]);

  return (
    <div className="stats">
      <button
        type="button"
        className="icon-button stats-toggle"
        aria-label={open ? "Close stats" : "Open stats"}
        aria-expanded={open}
        title="Library stats"
        onClick={() => setOpen((value) => !value)}
      >
        <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
          <path fill="currentColor" d="M4 20h16v-2H4v2ZM6 16h3V9H6v7Zm5 0h3V4h-3v12Zm5 0h3v-5h-3v5Z" />
        </svg>
      </button>
      {open && (
        <div className="stats-panel">
          {error && <span className="reindex-error" role="alert">{error}</span>}
          {!error && !stats && <span>Loading…</span>}
          {stats && (
            <>
              <div className="stats-summary">
                <div><dt>Images</dt><dd>{stats.total.toLocaleString()}</dd></div>
                <div><dt>Total size</dt><dd>{formatBytes(stats.total_size)}</dd></div>
                <div><dt>With text</dt><dd>{stats.with_ocr_text.toLocaleString()}</dd></div>
                <div><dt>With objects</dt><dd>{stats.with_objects.toLocaleString()}</dd></div>
                <div><dt>Untagged</dt><dd>{stats.without_ocr_or_objects.toLocaleString()}</dd></div>
                <div>
                  <dt>Indexed</dt>
                  <dd>{formatDate(stats.indexed_at_min)} – {formatDate(stats.indexed_at_max)}</dd>
                </div>
              </div>

              {stats.by_format.length > 0 && (
                <>
                  <h3>By format</h3>
                  <Bars rows={stats.by_format.map((row) => ({ label: row.format, count: row.count }))} />
                </>
              )}

              {stats.by_year.length > 0 && (
                <>
                  <h3>By year taken</h3>
                  <Bars rows={stats.by_year.map((row) => ({ label: row.year, count: row.count }))} />
                </>
              )}

              {stats.largest.length > 0 && (
                <>
                  <h3>Largest files</h3>
                  <ul className="stats-largest">
                    {stats.largest.map((row) => (
                      <li key={row.id}>
                        <span title={row.path}>{filename(row.path)}</span>
                        <span>{formatBytes(row.size)}</span>
                      </li>
                    ))}
                  </ul>
                </>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}
