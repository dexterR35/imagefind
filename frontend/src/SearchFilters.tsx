import { useEffect, useState } from "react";
import { fetchColors, fetchObjects, SearchFilters as Filters } from "./api";

interface Props {
  onChange: (filters: Filters) => void;
}

export function SearchFilters({ onChange }: Props) {
  const [colors, setColors] = useState<string[]>([]);
  const [objects, setObjects] = useState<string[]>([]);
  const [text, setText] = useState("");
  const [color, setColor] = useState<string | undefined>(undefined);
  const [object, setObject] = useState<string | undefined>(undefined);

  useEffect(() => {
    fetchColors().then(setColors);
    fetchObjects().then(setObjects);
  }, []);

  useEffect(() => {
    onChange({ text: text || undefined, color, object });
  }, [text, color, object]);

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
