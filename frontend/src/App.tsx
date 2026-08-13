import { useCallback, useState } from "react";
import { findSimilar, search, type ImageResult, type SearchFilters as Filters } from "./api";
import { ImageGrid } from "./ImageGrid";
import { ImageModal } from "./ImageModal";
import { ReindexButton } from "./ReindexButton";
import { SearchFilters } from "./SearchFilters";

export default function App() {
  const [images, setImages] = useState<ImageResult[]>([]);
  const [selected, setSelected] = useState<ImageResult | null>(null);
  const [filters, setFilters] = useState<Filters>({});

  const runSearch = useCallback(async (f: Filters) => {
    setFilters(f);
    const results = await search(f);
    setImages(results);
  }, []);

  async function handleFindSimilar(id: string) {
    const results = await findSimilar(id);
    setImages(results);
    setSelected(null);
  }

  return (
    <div className="app">
      <h1>ImageFind</h1>
      <SearchFilters onChange={runSearch} />
      <ReindexButton onComplete={() => runSearch(filters)} />
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
