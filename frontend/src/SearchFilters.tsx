import { useEffect, useState } from "react";
import { fetchColors, fetchObjects, type SearchFilters as Filters } from "./api";

interface Props {
  onChange: (filters: Filters) => void;
}

const DEBOUNCE_MS = 300;

export function SearchFilters({ onChange }: Props) {
  const [colors, setColors] = useState<string[]>([]);
  const [objects, setObjects] = useState<string[]>([]);
  const [text, setText] = useState("");
  const [debouncedText, setDebouncedText] = useState("");
  const [color, setColor] = useState<string | undefined>(undefined);
  const [object, setObject] = useState<string | undefined>(undefined);

  useEffect(() => {
    fetchColors().then(setColors);
    fetchObjects().then(setObjects);
  }, []);

  // Debounce the free-text input only: every keystroke would otherwise trigger
  // a real CLIP forward pass server-side, and slow responses for stale partial
  // queries could race with (and overwrite) a later, more complete one.
  useEffect(() => {
    const handle = window.setTimeout(() => setDebouncedText(text), DEBOUNCE_MS);
    return () => window.clearTimeout(handle);
  }, [text]);

  useEffect(() => {
    onChange({ text: debouncedText || undefined, color, object });
  }, [debouncedText, color, object]);

  return (
    <div className="search-filters">
      <input
        type="text"
        placeholder="Search text or meaning..."
        value={text}
        onChange={(e) => setText(e.target.value)}
      />
      <div className="color-swatches">
        {colors.map((c) => (
          <button
            key={c}
            type="button"
            aria-label={c}
            className={c === color ? "swatch selected" : "swatch"}
            style={{ backgroundColor: c }}
            onClick={() => setColor(color === c ? undefined : c)}
          />
        ))}
      </div>
      <select value={object ?? ""} onChange={(e) => setObject(e.target.value || undefined)}>
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
