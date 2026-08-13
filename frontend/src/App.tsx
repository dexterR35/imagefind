import { useCallback, useRef, useState } from "react";
import { findSimilar, search, type ImageResult, type SearchFilters as Filters } from "./api";
import { ImageGrid } from "./ImageGrid";
import { ImageModal } from "./ImageModal";
import { ReindexButton } from "./ReindexButton";
import { SearchFilters } from "./SearchFilters";
import "./App.css";

export default function App() {
  const [images, setImages] = useState<ImageResult[]>([]);
  const [selected, setSelected] = useState<ImageResult | null>(null);
  const [filters, setFilters] = useState<Filters>({});
  const [error, setError] = useState<string | null>(null);
  const requestId = useRef(0);

  const runSearch = useCallback(async (f: Filters) => {
    setFilters(f);
    const id = ++requestId.current;
    try {
      const results = await search(f);
      if (id !== requestId.current) return; // a newer search already superseded this one
      setImages(results);
      setError(null);
    } catch {
      if (id !== requestId.current) return;
      setError("Search failed. Please try again.");
    }
  }, []);

  async function handleFindSimilar(id: string) {
    const reqId = ++requestId.current;
    try {
      const results = await findSimilar(id);
      if (reqId !== requestId.current) return;
      setImages(results);
      setSelected(null);
      setError(null);
    } catch {
      if (reqId !== requestId.current) return;
      setError("Find Similar failed. Please try again.");
    }
  }

  return (
    <div className="app">
      <h1>ImageFind</h1>
      <SearchFilters onChange={runSearch} />
      <ReindexButton onComplete={() => runSearch(filters)} />
      {error && <p className="error-banner">{error}</p>}
      <ImageGrid images={images} onSelect={setSelected} />
      {selected && (
        <ImageModal
          image={selected}
          onClose={() => setSelected(null)}
          onFindSimilar={handleFindSimilar}
        />
      )}
    </div>
  );
}
