import { useEffect, useState } from "react";
import { fetchObjects, type SearchFilters as Filters } from "./api";

interface Props {
  onChange: (filters: Filters) => void;
}

const DEBOUNCE_MS = 300;

export function SearchFilters({ onChange }: Props) {
  const [objects, setObjects] = useState<string[]>([]);
  const [text, setText] = useState("");
  const [debouncedText, setDebouncedText] = useState("");
  const [object, setObject] = useState<string | undefined>(undefined);

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

  // Debounce the free-text input only: every keystroke would otherwise trigger
  // a server round-trip, and slow responses for stale partial queries could
  // race with (and overwrite) a later, more complete one.
  useEffect(() => {
    const handle = window.setTimeout(() => setDebouncedText(text), DEBOUNCE_MS);
    return () => window.clearTimeout(handle);
  }, [text]);

  useEffect(() => {
    onChange({ text: debouncedText || undefined, object });
  }, [debouncedText, object, onChange]);

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
      <select aria-label="Filter by object" value={object ?? ""} onChange={(e) => setObject(e.target.value || undefined)}>
        <option value="">All objects</option>
        {objects.map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
      </select>
    </div>
  );
}
