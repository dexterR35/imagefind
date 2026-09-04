import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  addToCollection,
  AUTH_REQUIRED_EVENT,
  bulkAddTags,
  bulkSetFavorite,
  fetchAuthSession,
  exportUrl,
  fetchCollections,
  fetchUserTags,
  filtersToSearchParams,
  findSimilar,
  login,
  logout,
  search,
  searchParamsToFilters,
  setFavorite,
  setImageNote,
  setImageTags,
  SORT_OPTIONS,
  type AuthSessionStatus,
  type Collection,
  type ImageResult,
  type SearchFilters as Filters,
  type SortOption,
} from "./api";
import { BrandMark } from "./BrandMark";
import { BulkActionBar } from "./BulkActionBar";
import { Collections } from "./Collections";
import { Duplicates } from "./Duplicates";
import { ImageGrid, type ResultView } from "./ImageGrid";
import { LoginScreen } from "./LoginScreen";
import { ImageModal } from "./ImageModal";
import { Pagination } from "./Pagination";
import { SearchFilters } from "./SearchFilters";
import { Settings } from "./Settings";
import { Stats } from "./Stats";
import "./App.css";

const PAGE_SIZE = 60;

interface UrlState {
  filters: Filters;
  sort: SortOption;
  view: ResultView;
  page: number;
}

function parseLocation(): UrlState {
  const params = new URLSearchParams(window.location.search);
  const sortRaw = params.get("sort");
  const pageRaw = Number(params.get("page"));
  return {
    filters: searchParamsToFilters(params),
    sort: SORT_OPTIONS.includes(sortRaw as SortOption) ? (sortRaw as SortOption) : "date_desc",
    view: params.get("view") === "table" ? "table" : "cards",
    page: Number.isInteger(pageRaw) && pageRaw >= 1 ? pageRaw : 1,
  };
}

function Gallery({ onLogout }: { onLogout: () => void }) {
  const initial = useMemo(parseLocation, []);
  const [images, setImages] = useState<ImageResult[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  // An image opened from a panel (e.g. Duplicates) that is not in the current
  // result grid — used as the modal's image when the id isn't found in `images`.
  const [modalOverride, setModalOverride] = useState<ImageResult | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => new Set());
  const [collections, setCollections] = useState<Collection[]>([]);
  const [userTags, setUserTags] = useState<string[]>([]);
  const [filters, setFilters] = useState<Filters>(initial.filters);
  const [sort, setSort] = useState<SortOption>(initial.sort);
  const [view, setView] = useState<ResultView>(initial.view);
  const [page, setPage] = useState(initial.page);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [showingSimilar, setShowingSimilar] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Bumping the key remounts SearchFilters so its inputs re-seed from the URL
  // after a back/forward navigation.
  const [seedFilters, setSeedFilters] = useState<Filters>(initial.filters);
  const [seedKey, setSeedKey] = useState(0);
  const requestId = useRef(0);
  const filtersRef = useRef<Filters>(initial.filters);
  const sortRef = useRef<SortOption>(initial.sort);
  // Consumed by the first search (and by a popstate) so an initial ?page=N is
  // honored; every later filter change resets to page 1.
  const pendingPageRef = useRef<number | null>(initial.page);
  const abortRef = useRef<AbortController | null>(null);
  const isTunnelAccess = window.location.hostname.endsWith(".trycloudflare.com");

  useEffect(() => () => abortRef.current?.abort(), []);

  // Keep the browser URL in sync so a search is shareable and survives reload.
  // Find Similar is a transient view with no query, so it is left out.
  useEffect(() => {
    if (showingSimilar) return;
    const params = filtersToSearchParams(filters);
    if (sort !== "date_desc") params.set("sort", sort);
    if (view !== "cards") params.set("view", view);
    if (page > 1) params.set("page", String(page));
    const qs = params.toString();
    const next = qs ? `?${qs}` : window.location.pathname;
    if (`${window.location.pathname}${window.location.search}` !== next) {
      window.history.replaceState(null, "", next);
    }
  }, [filters, sort, view, page, showingSimilar]);

  useEffect(() => {
    const onPopState = () => {
      const parsed = parseLocation();
      setSort(parsed.sort);
      sortRef.current = parsed.sort;
      setView(parsed.view);
      pendingPageRef.current = parsed.page;
      setSeedFilters(parsed.filters);
      setSeedKey((key) => key + 1);
    };
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  const runSearch = useCallback(async (f: Filters, nextSort: SortOption, nextPage: number) => {
    setFilters(f);
    filtersRef.current = f;
    setPage(nextPage);
    setShowingSimilar(false);
    setSelectedIds(new Set());
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
    const nextPage = pendingPageRef.current ?? 1;
    pendingPageRef.current = null;
    void runSearch(nextFilters, sortRef.current, nextPage);
  }, [runSearch]);

  const refreshCollections = useCallback(() => {
    fetchCollections().then(setCollections).catch(() => setCollections([]));
  }, []);

  useEffect(() => {
    refreshCollections();
    fetchUserTags().then(setUserTags).catch(() => setUserTags([]));
  }, [refreshCollections]);

  const patchImage = useCallback((id: string, patch: Partial<ImageResult>) => {
    setImages((list) => list.map((img) => (img.id === id ? { ...img, ...patch } : img)));
    setModalOverride((current) => (current && current.id === id ? { ...current, ...patch } : current));
  }, []);

  const handleToggleFavorite = useCallback((id: string, next: boolean) => {
    patchImage(id, { favorite: next });
    setFavorite(id, next).catch(() => patchImage(id, { favorite: !next }));
  }, [patchImage]);

  const handleTagsChange = useCallback((id: string, tags: string[]) => {
    const previous = images.find((img) => img.id === id)?.user_tags ?? [];
    patchImage(id, { user_tags: tags });
    setImageTags(id, tags)
      .then((saved) => {
        patchImage(id, { user_tags: saved });
        fetchUserTags().then(setUserTags).catch(() => {});
      })
      .catch(() => patchImage(id, { user_tags: previous }));
  }, [images, patchImage]);

  const handleNoteChange = useCallback((id: string, note: string) => {
    const previous = images.find((img) => img.id === id)?.note ?? "";
    patchImage(id, { note });
    setImageNote(id, note)
      .then((saved) => patchImage(id, { note: saved }))
      .catch(() => patchImage(id, { note: previous }));
  }, [images, patchImage]);

  const toggleSelect = useCallback((id: string) => {
    setSelectedIds((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const patchMany = useCallback((ids: string[], patch: (img: ImageResult) => Partial<ImageResult>) => {
    const idSet = new Set(ids);
    setImages((list) => list.map((img) => (idSet.has(img.id) ? { ...img, ...patch(img) } : img)));
  }, []);

  const handleBulkFavorite = useCallback((favorite: boolean) => {
    const ids = [...selectedIds];
    if (ids.length === 0) return;
    patchMany(ids, () => ({ favorite }));
    bulkSetFavorite(ids, favorite).catch(() => patchMany(ids, () => ({ favorite: !favorite })));
  }, [selectedIds, patchMany]);

  const handleBulkAddTags = useCallback((tags: string[]) => {
    const ids = [...selectedIds];
    if (ids.length === 0 || tags.length === 0) return;
    const before = new Map(images.filter((img) => ids.includes(img.id)).map((img) => [img.id, img.user_tags ?? []]));
    patchMany(ids, (img) => ({
      user_tags: Array.from(new Set([...(img.user_tags ?? []), ...tags])).sort((a, b) =>
        a.toLowerCase().localeCompare(b.toLowerCase()),
      ),
    }));
    bulkAddTags(ids, tags)
      .then(() => fetchUserTags().then(setUserTags).catch(() => {}))
      .catch(() => {
        // Roll the optimistic tags back (e.g. a tag was rejected as too long).
        patchMany(ids, (img) => ({ user_tags: before.get(img.id) ?? img.user_tags }));
      });
  }, [selectedIds, images, patchMany]);

  const handleBulkAddToCollection = useCallback((collectionId: string) => {
    const ids = [...selectedIds];
    if (ids.length === 0) return;
    addToCollection(collectionId, ids).then(refreshCollections).catch(() => {});
  }, [selectedIds, refreshCollections]);

  const handleAddToCollection = useCallback((collectionId: string, imageId: string) => {
    addToCollection(collectionId, [imageId]).then(refreshCollections).catch(() => {});
  }, [refreshCollections]);

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
      setSelectedId(null);
      setModalOverride(null);
      setSelectedIds(new Set());
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
  const selectedInGrid = images.find((img) => img.id === selectedId) ?? null;
  const selected =
    selectedInGrid ?? (selectedId && modalOverride?.id === selectedId ? modalOverride : null);
  const selectedIndex = selectedInGrid
    ? images.findIndex((img) => img.id === selectedInGrid.id)
    : -1;
  const isSemantic = filters.mode === "semantic";

  return (
    <div className="app">
      <header className="app-header">
        <div className="brand">
          <BrandMark />
          <h1>ImageFind</h1>
          <span className="env-badge">{isTunnelAccess ? "TUNNEL" : "LOCAL"}</span>
        </div>
        <div className="header-actions">
          <Duplicates onOpen={(img) => { setModalOverride(img); setSelectedId(img.id); }} />
          <Collections collections={collections} onChanged={refreshCollections} />
          <Stats />
          {!isTunnelAccess && (
            <Settings onReindexComplete={() => runSearch(filters, sort, 1)} />
          )}
          <button type="button" className="logout-button" onClick={onLogout}>Log out</button>
        </div>
      </header>
      <SearchFilters
        key={seedKey}
        initialFilters={seedFilters}
        onChange={handleFiltersChange}
        collections={collections}
        userTags={userTags}
      />
      {error && <p className="error-banner">{error}</p>}
      <div className="results-toolbar">
        <p className="result-count" aria-live="polite">
          {loading
            ? "Loading images…"
            : showingSimilar
              ? `${total} similar ${total === 1 ? "image" : "images"}`
              : isSemantic
                ? `${total} closest ${total === 1 ? "match" : "matches"}`
                : `${rangeStart}–${rangeEnd} of ${total} ${total === 1 ? "image" : "images"}`}
        </p>
        <div className="toolbar-controls">
          {!showingSimilar && !isSemantic && total > 0 && (
            <details className="export-menu">
              <summary>Export</summary>
              <div className="export-menu-links">
                <a href={exportUrl(filters, sort, "csv")} download>CSV</a>
                <a href={exportUrl(filters, sort, "json")} download>JSON</a>
              </div>
            </details>
          )}
          <div className="view-toggle" role="group" aria-label="Result view">
            <button
              type="button"
              aria-pressed={view === "cards"}
              onClick={() => setView("cards")}
            >
              Cards
            </button>
            <button
              type="button"
              aria-pressed={view === "table"}
              onClick={() => setView("table")}
            >
              Table
            </button>
          </div>
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
      </div>
      {loading && images.length === 0 ? (
        <div className="loading-grid" aria-hidden="true">
          {Array.from({ length: 12 }, (_, index) => <span key={index} />)}
        </div>
      ) : (
        <ImageGrid
          images={images}
          view={view}
          onSelect={(img) => setSelectedId(img.id)}
          onToggleFavorite={handleToggleFavorite}
          selectedIds={selectedIds}
          onToggleSelect={toggleSelect}
        />
      )}
      {!showingSimilar && !isSemantic && (
        <Pagination page={page} pageCount={pageCount} onChange={handlePageChange} />
      )}
      {selectedIds.size > 0 && (
        <BulkActionBar
          ids={[...selectedIds]}
          collections={collections}
          onFavorite={handleBulkFavorite}
          onAddTags={handleBulkAddTags}
          onAddToCollection={handleBulkAddToCollection}
          onClear={() => setSelectedIds(new Set())}
        />
      )}
      {selected && (
        <ImageModal
          image={selected}
          onClose={() => { setSelectedId(null); setModalOverride(null); }}
          onFindSimilar={handleFindSimilar}
          onPrev={selectedIndex > 0 ? () => setSelectedId(images[selectedIndex - 1].id) : undefined}
          onNext={
            selectedIndex >= 0 && selectedIndex < images.length - 1
              ? () => setSelectedId(images[selectedIndex + 1].id)
              : undefined
          }
          onToggleFavorite={handleToggleFavorite}
          onTagsChange={handleTagsChange}
          onNoteChange={handleNoteChange}
          collections={collections}
          onAddToCollection={handleAddToCollection}
        />
      )}
    </div>
  );
}

export default function App() {
  const [session, setSession] = useState<AuthSessionStatus | null>(null);
  const [sessionError, setSessionError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let active = true;
    setSessionError(null);
    fetchAuthSession()
      .then((status) => { if (active) setSession(status); })
      .catch(() => { if (active) setSessionError("Cannot connect to the ImageFind backend."); });
    return () => { active = false; };
  }, [attempt]);

  useEffect(() => {
    const requireLogin = () => setSession({ authenticated: false, configured: true });
    window.addEventListener(AUTH_REQUIRED_EVENT, requireLogin);
    return () => window.removeEventListener(AUTH_REQUIRED_EVENT, requireLogin);
  }, []);

  async function handleLogin(password: string) {
    const status = await login(password);
    setSession(status);
  }

  async function handleLogout() {
    try {
      await logout();
    } finally {
      setSession({ authenticated: false, configured: true });
    }
  }

  if (sessionError) {
    return (
      <main className="auth-page">
        <section className="auth-card" role="alert">
          <BrandMark size={22} />
          <h1>ImageFind</h1>
          <p className="auth-error">{sessionError}</p>
          <button type="button" className="btn-primary" onClick={() => setAttempt((value) => value + 1)}>Retry</button>
        </section>
      </main>
    );
  }
  if (session === null) {
    return <main className="auth-page"><p className="auth-loading">Checking secure session…</p></main>;
  }
  if (!session.authenticated) {
    return <LoginScreen configured={session.configured} onLogin={handleLogin} />;
  }
  return <Gallery onLogout={handleLogout} />;
}
