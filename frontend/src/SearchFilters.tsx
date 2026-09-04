import { useEffect, useMemo, useState } from "react";
import {
  fetchObjects,
  type Collection,
  type DateField,
  type Orientation,
  type SearchFilters as Filters,
} from "./api";

interface Props {
  onChange: (filters: Filters) => void;
  initialFilters?: Filters;
  collections?: Collection[];
  userTags?: string[];
}

const DEBOUNCE_MS = 300;
// The formats the indexer ingests (backend IMAGE_EXTENSIONS). heic/heif also
// work when the optional pillow-heif package is installed on the server.
const FORMATS = ["png", "jpg", "webp", "bmp", "gif", "tiff", "avif", "heic"];
const DATE_FIELDS: { value: DateField; label: string }[] = [
  { value: "date_taken", label: "Date taken" },
  { value: "mtime", label: "Modified" },
  { value: "indexed_at", label: "Indexed" },
];
const ORIENTATIONS: { value: Orientation; label: string }[] = [
  { value: "landscape", label: "Landscape" },
  { value: "portrait", label: "Portrait" },
  { value: "square", label: "Square" },
];

// "YYYY-MM-DD" from <input type="date"> to a UTC unix timestamp. `endOfDay`
// pushes it to 23:59:59 so a "to" bound includes the whole day.
function dateToEpoch(value: string, endOfDay: boolean): number | undefined {
  if (!value) return undefined;
  const ms = Date.parse(`${value}T00:00:00Z`);
  if (Number.isNaN(ms)) return undefined;
  return Math.floor(ms / 1000) + (endOfDay ? 86_399 : 0);
}

// Inverse, for seeding the date inputs from a shared URL.
function epochToDateInput(seconds: number | undefined): string {
  if (seconds === undefined || !Number.isFinite(seconds)) return "";
  return new Date(seconds * 1000).toISOString().slice(0, 10);
}

export function SearchFilters({ onChange, initialFilters, collections = [], userTags = [] }: Props) {
  const [objects, setObjects] = useState<string[]>([]);
  const [text, setText] = useState(() => initialFilters?.text ?? "");
  const [object, setObject] = useState<string | undefined>(initialFilters?.object);
  const [format, setFormat] = useState<string | undefined>(initialFilters?.format);
  const [orientation, setOrientation] = useState<Orientation | undefined>(
    initialFilters?.orientation,
  );
  const [favorite, setFavorite] = useState<boolean>(!!initialFilters?.favorite);
  const [collection, setCollection] = useState<string | undefined>(initialFilters?.collection);
  const [userTag, setUserTag] = useState<string | undefined>(initialFilters?.userTag);
  const [semantic, setSemantic] = useState<boolean>(initialFilters?.mode === "semantic");
  const [dateField, setDateField] = useState<DateField>(
    initialFilters?.dateField ?? "date_taken",
  );
  const [dateFrom, setDateFrom] = useState(() => epochToDateInput(initialFilters?.dateFrom));
  const [dateTo, setDateTo] = useState(() => epochToDateInput(initialFilters?.dateTo));

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
    if (orientation) next.orientation = orientation;
    if (favorite) next.favorite = true;
    if (collection) next.collection = collection;
    if (userTag) next.userTag = userTag;
    if (semantic && text.trim()) next.mode = "semantic";
    const from = dateToEpoch(dateFrom, false);
    const to = dateToEpoch(dateTo, true);
    if (from !== undefined) next.dateFrom = from;
    if (to !== undefined) next.dateTo = to;
    if (from !== undefined || to !== undefined) next.dateField = dateField;
    return next;
  }, [
    text, object, format, orientation, favorite, collection, userTag, semantic,
    dateField, dateFrom, dateTo,
  ]);

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

      <div className="match-toggle" role="group" aria-label="Match mode">
        <button type="button" aria-pressed={!semantic} onClick={() => setSemantic(false)}>Exact</button>
        <button
          type="button"
          aria-pressed={semantic}
          title="Rank by visual meaning (CLIP), ignoring the other filters"
          onClick={() => setSemantic(true)}
        >
          Fuzzy
        </button>
      </div>

      <label className="favorites-toggle">
        <input
          type="checkbox"
          checked={favorite}
          onChange={(e) => setFavorite(e.target.checked)}
        />
        <span>★ Favorites</span>
      </label>

      <details className="filter-advanced">
        <summary>More filters</summary>
        <div className="filter-grid">
          <fieldset>
            <legend>Collection</legend>
            <select
              aria-label="Filter by collection"
              value={collection ?? ""}
              onChange={(e) => setCollection(e.target.value || undefined)}
            >
              <option value="">All collections</option>
              {collections.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name} ({c.count})
                </option>
              ))}
            </select>
          </fieldset>

          <fieldset>
            <legend>Your tag</legend>
            <select
              aria-label="Filter by your tag"
              value={userTag ?? ""}
              onChange={(e) => setUserTag(e.target.value || undefined)}
            >
              <option value="">Any tag</option>
              {userTags.map((tag) => (
                <option key={tag} value={tag}>
                  {tag}
                </option>
              ))}
            </select>
          </fieldset>

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
            <legend>Orientation</legend>
            <select
              aria-label="Filter by orientation"
              value={orientation ?? ""}
              onChange={(e) => setOrientation((e.target.value || undefined) as Orientation | undefined)}
            >
              <option value="">Any orientation</option>
              {ORIENTATIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
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
