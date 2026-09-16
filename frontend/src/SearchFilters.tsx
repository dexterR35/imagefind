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
  { value: "added_at", label: "Added" },
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
  // Open on load when a shared link already carries one of the advanced facets.
  const [advancedOpen, setAdvancedOpen] = useState(() => Boolean(
    initialFilters?.object || initialFilters?.format || initialFilters?.orientation ||
    initialFilters?.collection || initialFilters?.userTag ||
    initialFilters?.dateFrom || initialFilters?.dateTo,
  ));

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

  // Everything set beyond the always-visible row, echoed back as removable chips.
  const chips: { key: string; label: string; clear: () => void }[] = [];
  if (collection) {
    const name = collections.find((c) => c.id === collection)?.name ?? collection;
    chips.push({ key: "collection", label: `Collection: ${name}`, clear: () => setCollection(undefined) });
  }
  if (userTag) chips.push({ key: "tag", label: `Tag: ${userTag}`, clear: () => setUserTag(undefined) });
  if (object) chips.push({ key: "object", label: `Object: ${object}`, clear: () => setObject(undefined) });
  if (format) chips.push({ key: "format", label: format.toUpperCase(), clear: () => setFormat(undefined) });
  if (orientation) {
    const label = ORIENTATIONS.find((o) => o.value === orientation)?.label ?? orientation;
    chips.push({ key: "orientation", label, clear: () => setOrientation(undefined) });
  }
  if (dateFrom || dateTo) {
    const field = DATE_FIELDS.find((d) => d.value === dateField)?.label ?? dateField;
    chips.push({
      key: "date",
      label: `${field}: ${dateFrom || "…"} → ${dateTo || "…"}`,
      clear: () => { setDateFrom(""); setDateTo(""); },
    });
  }

  function clearAll() {
    setCollection(undefined);
    setUserTag(undefined);
    setObject(undefined);
    setFormat(undefined);
    setOrientation(undefined);
    setDateFrom("");
    setDateTo("");
    setFavorite(false);
  }

  return (
    <div className="search-filters">
      <div className="filter-row">
        <div className="search-box">
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" />
          </svg>
          <input
            type="text"
            aria-label="Search images"
            placeholder="chair, person, dog…"
            maxLength={200}
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
          {text && (
            <button type="button" className="search-clear" aria-label="Clear search" onClick={() => setText("")}>×</button>
          )}
        </div>

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

        <button
          type="button"
          className="filter-toggle"
          aria-expanded={advancedOpen}
          aria-controls="filter-fields"
          onClick={() => setAdvancedOpen((open) => !open)}
        >
          Filters
          {chips.length > 0 && <span className="filter-count">{chips.length}</span>}
        </button>

        {/* Always mounted, so the selects keep their state while collapsed. */}
        <div id="filter-fields" className={`filter-fields${advancedOpen ? " is-open" : ""}`}>
          <label className="filter-field">
            <span>Collection</span>
            <select
              aria-label="Filter by collection"
              value={collection ?? ""}
              onChange={(e) => setCollection(e.target.value || undefined)}
            >
              <option value="">All</option>
              {collections.map((c) => (
                <option key={c.id} value={c.id}>{c.name} ({c.count})</option>
              ))}
            </select>
          </label>

          <label className="filter-field">
            <span>Tag</span>
            <select
              aria-label="Filter by your tag"
              value={userTag ?? ""}
              onChange={(e) => setUserTag(e.target.value || undefined)}
            >
              <option value="">Any</option>
              {userTags.map((tag) => <option key={tag} value={tag}>{tag}</option>)}
            </select>
          </label>

          <label className="filter-field">
            <span>Object</span>
            <select
              aria-label="Filter by object"
              value={object ?? ""}
              onChange={(e) => setObject(e.target.value || undefined)}
            >
              <option value="">All</option>
              {objects.map((o) => <option key={o} value={o}>{o}</option>)}
            </select>
          </label>

          <label className="filter-field">
            <span>Format</span>
            <select
              aria-label="Filter by format"
              value={format ?? ""}
              onChange={(e) => setFormat(e.target.value || undefined)}
            >
              <option value="">All</option>
              {FORMATS.map((f) => <option key={f} value={f}>{f.toUpperCase()}</option>)}
            </select>
          </label>

          <label className="filter-field">
            <span>Shape</span>
            <select
              aria-label="Filter by orientation"
              value={orientation ?? ""}
              onChange={(e) => setOrientation((e.target.value || undefined) as Orientation | undefined)}
            >
              <option value="">Any</option>
              {ORIENTATIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </label>

          <div className="filter-field is-range">
            <select
              aria-label="Date field"
              value={dateField}
              onChange={(e) => setDateField(e.target.value as DateField)}
            >
              {DATE_FIELDS.map((d) => <option key={d.value} value={d.value}>{d.label}</option>)}
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
          </div>
        </div>
      </div>

      {(chips.length > 0 || favorite) && (
        <div className="filter-chips">
          {favorite && (
            <span className="filter-chip">
              ★ Favorites
              <button type="button" aria-label="Remove favorites filter" onClick={() => setFavorite(false)}>×</button>
            </span>
          )}
          {chips.map((chip) => (
            <span className="filter-chip" key={chip.key}>
              {chip.label}
              <button type="button" aria-label={`Remove ${chip.key} filter`} onClick={chip.clear}>×</button>
            </span>
          ))}
          <button type="button" className="filter-clear-all" onClick={clearAll}>Clear all</button>
        </div>
      )}
    </div>
  );
}
