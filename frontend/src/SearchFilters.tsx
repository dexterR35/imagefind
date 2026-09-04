import { useEffect, useMemo, useState } from "react";
import { fetchObjects, type DateField, type SearchFilters as Filters } from "./api";

interface Props {
  onChange: (filters: Filters) => void;
}

const DEBOUNCE_MS = 300;
// The formats the indexer ingests (backend IMAGE_EXTENSIONS).
const FORMATS = ["png", "jpg", "webp", "bmp"];
const DATE_FIELDS: { value: DateField; label: string }[] = [
  { value: "date_taken", label: "Date taken" },
  { value: "mtime", label: "Modified" },
  { value: "indexed_at", label: "Indexed" },
];

// "YYYY-MM-DD" from <input type="date"> to a UTC unix timestamp. `endOfDay`
// pushes it to 23:59:59 so a "to" bound includes the whole day.
function dateToEpoch(value: string, endOfDay: boolean): number | undefined {
  if (!value) return undefined;
  const ms = Date.parse(`${value}T00:00:00Z`);
  if (Number.isNaN(ms)) return undefined;
  return Math.floor(ms / 1000) + (endOfDay ? 86_399 : 0);
}

export function SearchFilters({ onChange }: Props) {
  const [objects, setObjects] = useState<string[]>([]);
  const [text, setText] = useState("");
  const [object, setObject] = useState<string | undefined>(undefined);
  const [format, setFormat] = useState<string | undefined>(undefined);
  const [dateField, setDateField] = useState<DateField>("date_taken");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");

  useEffect(() => {
    let active = true;
    fetchObjects()
      .then((nextObjects) => {
        if (!active) return;
        setObjects(nextObjects);
      })
      .catch(() => {
        // Filters are optional search aids. Keep the text search usable when
        // the backend is temporarily unavailable and avoid an unhandled
        // promise rejection in the browser.
        if (!active) return;
        setObjects([]);
      });
    return () => {
      active = false;
    };
  }, []);

  const filters = useMemo<Filters>(() => {
    const next: Filters = { text: text.trim() || undefined, object };
    if (format) next.format = format;
    const from = dateToEpoch(dateFrom, false);
    const to = dateToEpoch(dateTo, true);
    if (from !== undefined) next.dateFrom = from;
    if (to !== undefined) next.dateTo = to;
    if (from !== undefined || to !== undefined) next.dateField = dateField;
    return next;
  }, [text, object, format, dateField, dateFrom, dateTo]);

  // Debounce every filter, not just the text box: the date fields also change
  // rapidly while being typed, and each change is a server round-trip.
  const [applied, setApplied] = useState(filters);
  useEffect(() => {
    const handle = window.setTimeout(() => setApplied(filters), DEBOUNCE_MS);
    return () => window.clearTimeout(handle);
  }, [filters]);

  useEffect(() => {
    onChange(applied);
  }, [applied, onChange]);

  return (
    <div className="search-filters">
      <input
        type="text"
        aria-label="Search images"
        placeholder="chair, person, dog..."
        maxLength={200}
        value={text}
        onChange={(e) => setText(e.target.value)}
      />

      <details className="filter-advanced">
        <summary>More filters</summary>
        <div className="filter-grid">
          <fieldset>
            <legend>Object</legend>
            <select
              aria-label="Filter by object"
              value={object ?? ""}
              onChange={(e) => setObject(e.target.value || undefined)}
            >
              <option value="">All objects</option>
              {objects.map((o) => (
                <option key={o} value={o}>
                  {o}
                </option>
              ))}
            </select>
          </fieldset>

          <fieldset>
            <legend>Format</legend>
            <select
              aria-label="Filter by format"
              value={format ?? ""}
              onChange={(e) => setFormat(e.target.value || undefined)}
            >
              <option value="">All formats</option>
              {FORMATS.map((f) => (
                <option key={f} value={f}>
                  {f.toUpperCase()}
                </option>
              ))}
            </select>
          </fieldset>

          <fieldset>
            <legend>Date range</legend>
            <select
              aria-label="Date field"
              value={dateField}
              onChange={(e) => setDateField(e.target.value as DateField)}
            >
              {DATE_FIELDS.map((d) => (
                <option key={d.value} value={d.value}>
                  {d.label}
                </option>
              ))}
            </select>
            <input
              type="date" aria-label="From date"
              value={dateFrom} onChange={(e) => setDateFrom(e.target.value)}
            />
            <span aria-hidden="true">–</span>
            <input
              type="date" aria-label="To date"
              value={dateTo} onChange={(e) => setDateTo(e.target.value)}
            />
          </fieldset>
        </div>
      </details>
    </div>
  );
}
