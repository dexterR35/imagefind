import { useCallback, useEffect, useRef, useState } from "react";
import {
  findSimilar,
  search,
  type ImageResult,
  type SearchFilters as Filters,
  type SortOption,
} from "./api";
import { ImageGrid } from "./ImageGrid";
import { ImageModal } from "./ImageModal";
import { Pagination } from "./Pagination";
import { ReindexButton } from "./ReindexButton";
import { SearchFilters } from "./SearchFilters";
import { Settings } from "./Settings";
import "./App.css";

const PAGE_SIZE = 60;

export default function App() {
  const [images, setImages] = useState<ImageResult[]>([]);
  const [selected, setSelected] = useState<ImageResult | null>(null);
  const [filters, setFilters] = useState<Filters>({});
  const [sort, setSort] = useState<SortOption>("date_desc");
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [showingSimilar, setShowingSimilar] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const requestId = useRef(0);
  const filtersRef = useRef<Filters>({});
  const sortRef = useRef<SortOption>("date_desc");
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => () => abortRef.current?.abort(), []);

  const runSearch = useCallback(async (f: Filters, nextSort: SortOption, nextPage: number) => {
    setFilters(f);
    filtersRef.current = f;
    setPage(nextPage);
    setShowingSimilar(false);
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    const id = ++requestId.current;
    setLoading(true);
    try {
      const response = await search(f, {
        sort: nextSort,
        offset: (nextPage - 1) * PAGE_SIZE,
        limit: PAGE_SIZE,
        signal: controller.signal,
      });
      if (id !== requestId.current) return; // a newer search already superseded this one
      setImages(response.results);
      setTotal(response.total);
      setError(null);
    } catch {
      if (id !== requestId.current) return;
      setError("Search failed. Please try again.");
    } finally {
      if (id === requestId.current) setLoading(false);
    }
  }, []);

  const handleFiltersChange = useCallback((nextFilters: Filters) => {
    void runSearch(nextFilters, sortRef.current, 1);
  }, [runSearch]);

  function handleSortChange(nextSort: SortOption) {
    setSort(nextSort);
    sortRef.current = nextSort;
    void runSearch(filtersRef.current, nextSort, 1);
  }

  function handlePageChange(nextPage: number) {
    void runSearch(filtersRef.current, sortRef.current, nextPage);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  async function handleFindSimilar(id: string) {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    const reqId = ++requestId.current;
    setLoading(true);
    try {
      const results = await findSimilar(id, controller.signal);
      if (reqId !== requestId.current) return;
      setImages(results);
      setTotal(results.length);
      setPage(1);
      setShowingSimilar(true);
      setSelected(null);
      setError(null);
    } catch {
      if (reqId !== requestId.current) return;
      setError("Find Similar failed. Please try again.");
    } finally {
      if (reqId === requestId.current) setLoading(false);
    }
  }

  const pageCount = Math.ceil(total / PAGE_SIZE);
  const rangeStart = total === 0 ? 0 : (page - 1) * PAGE_SIZE + 1;
  const rangeEnd = Math.min(page * PAGE_SIZE, total);

  return (
    <div className="app">
      <h1>ImageFind</h1>
      <SearchFilters onChange={handleFiltersChange} />
      <div className="toolbar">
        <ReindexButton onComplete={() => runSearch(filters, sort, 1)} />
        <Settings onReindexComplete={() => runSearch(filters, sort, 1)} />
      </div>
      {error && <p className="error-banner">{error}</p>}
      <div className="results-toolbar">
        <p className="result-count" aria-live="polite">
          {loading
            ? "Loading images…"
            : showingSimilar
              ? `${total} similar ${total === 1 ? "image" : "images"}`
              : `${rangeStart}–${rangeEnd} of ${total} ${total === 1 ? "image" : "images"}`}
        </p>
        <label className="sort-control">
          <span>Sort by</span>
          <select value={sort} onChange={(event) => handleSortChange(event.target.value as SortOption)}>
            <option value="date_desc">Newest first</option>
            <option value="date_asc">Oldest first</option>
            <option value="name_asc">Name A–Z</option>
            <option value="name_desc">Name Z–A</option>
            <option value="size_desc">Largest first</option>
            <option value="size_asc">Smallest first</option>
          </select>
        </label>
      </div>
      {loading && images.length === 0 ? (
        <div className="loading-grid" aria-hidden="true">
          {Array.from({ length: 12 }, (_, index) => <span key={index} />)}
        </div>
      ) : (
        <ImageGrid images={images} onSelect={setSelected} />
      )}
      {!showingSimilar && (
        <Pagination page={page} pageCount={pageCount} onChange={handlePageChange} />
      )}
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
