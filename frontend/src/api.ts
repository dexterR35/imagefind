export interface ImageResult {
  id: string;
  path: string;
  thumbnail_url: string;
  ocr_text: string;
  objects: string[];
  width: number;
  height: number;
  format: string;
  size: number;
  mtime: number;
  date_taken: number;
  indexed_at: number;
  favorite?: boolean;
  user_tags?: string[];
  note?: string;
}

export interface Collection {
  id: string;
  name: string;
  created_at: number;
  count: number;
}

export type DateField = "date_taken" | "mtime" | "indexed_at";
export type Orientation = "portrait" | "landscape" | "square";

export interface SearchFilters {
  text?: string;
  object?: string;
  format?: string;
  sizeMin?: number; // bytes
  sizeMax?: number; // bytes
  dateField?: DateField;
  dateFrom?: number; // unix seconds
  dateTo?: number; // unix seconds
  widthMin?: number;
  widthMax?: number;
  heightMin?: number;
  heightMax?: number;
  orientation?: Orientation;
  favorite?: boolean;
  collection?: string;
  userTag?: string;
  mode?: "semantic";
}

export type SortOption =
  | "date_desc"
  | "date_asc"
  | "name_asc"
  | "name_desc"
  | "size_desc"
  | "size_asc";

export const SORT_OPTIONS: SortOption[] = [
  "date_desc",
  "date_asc",
  "name_asc",
  "name_desc",
  "size_desc",
  "size_asc",
];

export interface SearchOptions {
  sort?: SortOption;
  offset?: number;
  limit?: number;
  signal?: AbortSignal;
}

export interface SearchResponse {
  results: ImageResult[];
  total: number;
}

export interface CatalogStats {
  total: number;
  total_size: number;
  indexed_at_min: number | null;
  indexed_at_max: number | null;
  by_format: { format: string; count: number }[];
  by_year: { year: string; count: number }[];
  with_ocr_text: number;
  with_objects: number;
  without_ocr_or_objects: number;
  largest: { id: string; path: string; size: number }[];
}

export interface ReindexFailure {
  path: string;
  error: string;
}

export interface ReindexStatus {
  processed: number;
  total: number;
  failed: number;
  done: boolean;
  error: string | null;
  cancelled: boolean;
  failures?: ReindexFailure[];
}

export interface Settings {
  ram_confidence: number | null;
  ram_custom_tags: string[];
  images_dir: string;
}

export interface ModelStatus {
  installed: boolean;
}

export interface ModelDownloadStatus {
  downloaded_bytes: number;
  total_bytes: number;
  done: boolean;
  error: string | null;
  cancelled: boolean;
}

export interface AuthSessionStatus {
  authenticated: boolean;
  configured: boolean;
  expires_at?: number;
  csrf_token?: string;
}

export const AUTH_REQUIRED_EVENT = "imagefind:authentication-required";

// Development uses Vite's /api proxy. The production build is served by the
// backend itself, so it calls the root API paths on the same origin directly.
const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? (import.meta.env.PROD ? "" : "/api");
let csrfToken: string | null = null;

async function apiFetch(url: string, init?: RequestInit): Promise<Response> {
  let requestInit = init;
  const method = init?.method?.toUpperCase() ?? "GET";
  if (!["GET", "HEAD", "OPTIONS"].includes(method) && csrfToken) {
    const headers = new Headers(init?.headers);
    headers.set("X-CSRF-Token", csrfToken);
    requestInit = { ...init, headers };
  }
  const response = requestInit ? await fetch(url, requestInit) : await fetch(url);
  if (response.status === 401 && !url.endsWith("/auth/login")) {
    csrfToken = null;
    window.dispatchEvent(new Event(AUTH_REQUIRED_EVENT));
  }
  return response;
}

async function errorDetail(res: Response): Promise<string> {
  try {
    const body = await res.json();
    if (typeof body.detail === "string") return body.detail;
    if (Array.isArray(body.detail)) {
      return body.detail.map((d: { msg?: string }) => d.msg ?? JSON.stringify(d)).join("; ");
    }
  } catch {
    // response body wasn't JSON (or had no detail) — fall through to status text
  }
  return res.statusText || `status ${res.status}`;
}

export async function fetchObjects(): Promise<string[]> {
  const res = await apiFetch(`${BASE_URL}/objects`);
  if (!res.ok) throw new Error(`fetch objects failed: ${res.status}`);
  return res.json();
}

export async function fetchStats(): Promise<CatalogStats> {
  const res = await apiFetch(`${BASE_URL}/stats`);
  if (!res.ok) throw new Error(`fetch stats failed: ${res.status}`);
  return res.json();
}

function toAbsolute(results: ImageResult[]): ImageResult[] {
  return results.map((r) => ({ ...r, thumbnail_url: `${BASE_URL}${r.thumbnail_url}` }));
}

export function downloadUrl(imageId: string): string {
  return `${BASE_URL}/download/${imageId}`;
}

// Full-resolution original, for the detail view's zoom/pan preview.
export function imageUrl(imageId: string): string {
  return `${BASE_URL}/image/${imageId}`;
}

// A GET URL that streams the whole filtered result set as a file download.
// Used as an <a href> so the browser handles it with the session cookie.
export function exportUrl(
  filters: SearchFilters,
  sort: SortOption,
  output: "csv" | "json",
): string {
  const params = filtersToSearchParams(filters);
  if (sort !== "date_desc") params.set("sort", sort);
  params.set("output", output);
  return `${BASE_URL}/search/export?${params.toString()}`;
}

export interface IndexBackup {
  name: string;
  size: number;
  created_at: number;
}

export async function fetchBackups(): Promise<IndexBackup[]> {
  const res = await apiFetch(`${BASE_URL}/backup`);
  if (!res.ok) throw new Error(await errorDetail(res));
  return res.json();
}

export async function createBackup(): Promise<IndexBackup> {
  const res = await apiFetch(`${BASE_URL}/backup`, { method: "POST" });
  if (!res.ok) throw new Error(await errorDetail(res));
  return res.json();
}

const NUMERIC_FILTER_PARAMS: [keyof SearchFilters, string][] = [
  ["sizeMin", "size_min"],
  ["sizeMax", "size_max"],
  ["dateFrom", "date_from"],
  ["dateTo", "date_to"],
  ["widthMin", "width_min"],
  ["widthMax", "width_max"],
  ["heightMin", "height_min"],
  ["heightMax", "height_max"],
];

// The query-string form of a filter set — shared by the API call and the
// browser URL so a search is reproducible from a shared link.
export function filtersToSearchParams(filters: SearchFilters): URLSearchParams {
  const params = new URLSearchParams();
  if (filters.text) params.set("text", filters.text);
  if (filters.object) params.set("object", filters.object);
  if (filters.format) params.set("format", filters.format);
  if (filters.sizeMin !== undefined) params.set("size_min", String(filters.sizeMin));
  if (filters.sizeMax !== undefined) params.set("size_max", String(filters.sizeMax));
  if (filters.dateFrom !== undefined) params.set("date_from", String(filters.dateFrom));
  if (filters.dateTo !== undefined) params.set("date_to", String(filters.dateTo));
  if (
    (filters.dateFrom !== undefined || filters.dateTo !== undefined) &&
    filters.dateField
  ) {
    params.set("date_field", filters.dateField);
  }
  if (filters.widthMin !== undefined) params.set("width_min", String(filters.widthMin));
  if (filters.widthMax !== undefined) params.set("width_max", String(filters.widthMax));
  if (filters.heightMin !== undefined) params.set("height_min", String(filters.heightMin));
  if (filters.heightMax !== undefined) params.set("height_max", String(filters.heightMax));
  if (filters.orientation) params.set("orientation", filters.orientation);
  if (filters.favorite) params.set("favorite", "true");
  if (filters.collection) params.set("collection", filters.collection);
  if (filters.userTag) params.set("user_tag", filters.userTag);
  if (filters.mode === "semantic") params.set("mode", "semantic");
  return params;
}

const DATE_FIELDS: DateField[] = ["date_taken", "mtime", "indexed_at"];
const ORIENTATIONS: Orientation[] = ["portrait", "landscape", "square"];

// Inverse of filtersToSearchParams — tolerant of junk (unknown or malformed
// values are dropped rather than thrown).
export function searchParamsToFilters(params: URLSearchParams): SearchFilters {
  const filters: SearchFilters = {};
  const text = params.get("text")?.trim();
  if (text) filters.text = text;
  const object = params.get("object")?.trim();
  if (object) filters.object = object;
  const format = params.get("format")?.trim().toLowerCase();
  if (format) filters.format = format;
  for (const [key, param] of NUMERIC_FILTER_PARAMS) {
    const raw = params.get(param);
    if (raw === null) continue;
    const value = Number(raw);
    if (Number.isFinite(value) && value >= 0) {
      (filters[key] as number) = value;
    }
  }
  const dateField = params.get("date_field");
  if (dateField && DATE_FIELDS.includes(dateField as DateField)) {
    filters.dateField = dateField as DateField;
  }
  const orientation = params.get("orientation");
  if (orientation && ORIENTATIONS.includes(orientation as Orientation)) {
    filters.orientation = orientation as Orientation;
  }
  if (params.get("favorite") === "true") filters.favorite = true;
  const collection = params.get("collection")?.trim();
  if (collection) filters.collection = collection;
  const userTag = params.get("user_tag")?.trim();
  if (userTag) filters.userTag = userTag;
  if (params.get("mode") === "semantic") filters.mode = "semantic";
  return filters;
}

export async function search(
  filters: SearchFilters,
  options: SearchOptions = {},
): Promise<SearchResponse> {
  const params = filtersToSearchParams(filters);
  if (options.sort) params.set("sort", options.sort);
  if (options.offset !== undefined) params.set("offset", String(options.offset));
  if (options.limit !== undefined) params.set("limit", String(options.limit));
  const url = `${BASE_URL}/search?${params.toString()}`;
  const res = options.signal ? await apiFetch(url, { signal: options.signal }) : await apiFetch(url);
  if (!res.ok) throw new Error(`search failed: ${res.status}`);
  const data: SearchResponse = await res.json();
  return { ...data, results: toAbsolute(data.results) };
}

export async function findSimilar(imageId: string, signal?: AbortSignal): Promise<ImageResult[]> {
  const url = `${BASE_URL}/search/similar/${imageId}`;
  const res = signal ? await apiFetch(url, { signal }) : await apiFetch(url);
  if (!res.ok) throw new Error(`find similar failed: ${res.status}`);
  const data: ImageResult[] = await res.json();
  return toAbsolute(data);
}

export async function fetchDuplicates(threshold = 0.08): Promise<ImageResult[][]> {
  const res = await apiFetch(`${BASE_URL}/duplicates?threshold=${threshold}`);
  if (!res.ok) throw new Error(`find duplicates failed: ${res.status}`);
  const groups: ImageResult[][] = await res.json();
  return groups.map(toAbsolute);
}

// ---- Viewer curation: favorites, manual tags, notes, collections ---------

async function putJson<T>(url: string, body: unknown): Promise<T> {
  const res = await apiFetch(`${BASE_URL}${url}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(await errorDetail(res));
  return res.json();
}

export async function setFavorite(imageId: string, favorite: boolean): Promise<boolean> {
  const data = await putJson<{ favorite: boolean }>(`/images/${imageId}/favorite`, { favorite });
  return data.favorite;
}

export async function setImageTags(imageId: string, tags: string[]): Promise<string[]> {
  const data = await putJson<{ user_tags: string[] }>(`/images/${imageId}/tags`, { tags });
  return data.user_tags;
}

async function postJson<T>(url: string, body: unknown): Promise<T> {
  const res = await apiFetch(`${BASE_URL}${url}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(await errorDetail(res));
  return res.json();
}

export async function bulkSetFavorite(imageIds: string[], favorite: boolean): Promise<number> {
  const data = await postJson<{ changed: number }>("/images/favorite", { image_ids: imageIds, favorite });
  return data.changed;
}

export async function bulkAddTags(imageIds: string[], tags: string[]): Promise<number> {
  const data = await postJson<{ added: number }>("/images/tags/add", { image_ids: imageIds, tags });
  return data.added;
}

// A GET download URL for a zip of the selected originals (<a href download>).
export function zipDownloadUrl(imageIds: string[]): string {
  return `${BASE_URL}/download/zip?ids=${imageIds.join(",")}`;
}

export async function setImageNote(imageId: string, note: string): Promise<string> {
  const data = await putJson<{ note: string }>(`/images/${imageId}/note`, { note });
  return data.note;
}

export async function fetchUserTags(): Promise<string[]> {
  const res = await apiFetch(`${BASE_URL}/user-tags`);
  if (!res.ok) throw new Error(`fetch user tags failed: ${res.status}`);
  return res.json();
}

export async function fetchCollections(): Promise<Collection[]> {
  const res = await apiFetch(`${BASE_URL}/collections`);
  if (!res.ok) throw new Error(`fetch collections failed: ${res.status}`);
  return res.json();
}

export async function createCollection(name: string): Promise<Collection> {
  const res = await apiFetch(`${BASE_URL}/collections`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  if (!res.ok) throw new Error(await errorDetail(res));
  return res.json();
}

export async function renameCollection(id: string, name: string): Promise<void> {
  const res = await apiFetch(`${BASE_URL}/collections/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  if (!res.ok) throw new Error(await errorDetail(res));
}

export async function deleteCollection(id: string): Promise<void> {
  const res = await apiFetch(`${BASE_URL}/collections/${id}`, { method: "DELETE" });
  if (!res.ok) throw new Error(await errorDetail(res));
}

export async function addToCollection(id: string, imageIds: string[]): Promise<number> {
  const res = await apiFetch(`${BASE_URL}/collections/${id}/images`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ image_ids: imageIds }),
  });
  if (!res.ok) throw new Error(await errorDetail(res));
  return (await res.json()).added;
}

export async function removeFromCollection(id: string, imageIds: string[]): Promise<number> {
  const res = await apiFetch(`${BASE_URL}/collections/${id}/images`, {
    method: "DELETE",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ image_ids: imageIds }),
  });
  if (!res.ok) throw new Error(await errorDetail(res));
  return (await res.json()).removed;
}

export async function startReindex(force = false): Promise<string> {
  const res = await apiFetch(`${BASE_URL}/reindex?force=${force}`, { method: "POST" });
  if (!res.ok) throw new Error(await errorDetail(res));
  const data = await res.json();
  return data.job_id;
}

export async function fetchReindexStatus(jobId: string): Promise<ReindexStatus> {
  const res = await apiFetch(`${BASE_URL}/reindex/status/${jobId}`);
  if (!res.ok) throw new Error(`fetch reindex status failed: ${res.status}`);
  return res.json();
}

export async function cancelReindex(jobId: string): Promise<void> {
  const res = await apiFetch(`${BASE_URL}/reindex/${jobId}/cancel`, { method: "POST" });
  if (!res.ok) throw new Error(await errorDetail(res));
}

// Stream reindex progress via Server-Sent Events. Returns a stop fn, or null
// when EventSource is unavailable (e.g. the test environment) so the caller can
// fall back to polling fetchReindexStatus.
export function streamReindexStatus(
  jobId: string,
  onUpdate: (status: ReindexStatus) => void,
  onError: () => void,
): (() => void) | null {
  if (typeof EventSource === "undefined") return null;
  const source = new EventSource(`${BASE_URL}/reindex/status/${jobId}/stream`);
  let lastDone = false;
  source.onmessage = (event) => {
    try {
      const status: ReindexStatus = JSON.parse(event.data);
      lastDone = status.done;
      onUpdate(status);
      if (status.done) source.close();
    } catch {
      // ignore a malformed frame; the next one usually parses
    }
  };
  source.onerror = () => {
    // A transient drop leaves readyState CONNECTING while the browser retries —
    // don't tear that down. Only surface an error once it is permanently closed.
    if (source.readyState === EventSource.CLOSED && !lastDone) {
      onError();
    }
  };
  return () => source.close();
}

export async function fetchSettings(): Promise<Settings> {
  const res = await apiFetch(`${BASE_URL}/settings`);
  if (!res.ok) throw new Error(await errorDetail(res));
  return res.json();
}

export async function updateSettings(settings: Settings): Promise<Settings> {
  const res = await apiFetch(`${BASE_URL}/settings`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(settings),
  });
  if (!res.ok) throw new Error(await errorDetail(res));
  return res.json();
}

export async function fetchModelStatus(): Promise<ModelStatus> {
  const res = await apiFetch(`${BASE_URL}/model/status`);
  if (!res.ok) throw new Error(await errorDetail(res));
  return res.json();
}

export async function startModelDownload(): Promise<string> {
  const res = await apiFetch(`${BASE_URL}/model/download`, { method: "POST" });
  if (!res.ok) throw new Error(await errorDetail(res));
  const data = await res.json();
  return data.job_id;
}

export async function fetchModelDownloadStatus(jobId: string): Promise<ModelDownloadStatus> {
  const res = await apiFetch(`${BASE_URL}/model/download/status/${jobId}`);
  if (!res.ok) throw new Error(await errorDetail(res));
  return res.json();
}

export async function cancelModelDownload(jobId: string): Promise<void> {
  const res = await apiFetch(`${BASE_URL}/model/download/${jobId}/cancel`, { method: "POST" });
  if (!res.ok) throw new Error(await errorDetail(res));
}

export async function fetchAuthSession(): Promise<AuthSessionStatus> {
  const res = await apiFetch(`${BASE_URL}/auth/session`);
  if (!res.ok) throw new Error(await errorDetail(res));
  const status: AuthSessionStatus = await res.json();
  csrfToken = status.authenticated ? status.csrf_token ?? null : null;
  return status;
}

export async function login(password: string): Promise<AuthSessionStatus> {
  const res = await apiFetch(`${BASE_URL}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password }),
  });
  if (!res.ok) throw new Error(await errorDetail(res));
  const status: AuthSessionStatus = await res.json();
  csrfToken = status.csrf_token ?? null;
  return status;
}

export async function logout(): Promise<void> {
  const res = await apiFetch(`${BASE_URL}/auth/logout`, { method: "POST" });
  csrfToken = null;
  if (!res.ok) throw new Error(await errorDetail(res));
}
