export interface ImageResult {
  id: string;
  path: string;
  thumbnail_url: string;
  ocr_text: string;
  colors: string[];
  objects: string[];
  width: number;
  height: number;
  format: string;
  size: number;
  mtime: number;
  date_taken: number;
  indexed_at: number;
}

export interface SearchFilters {
  text?: string;
  color?: string;
  object?: string;
}

export type SortOption =
  | "date_desc"
  | "date_asc"
  | "name_asc"
  | "name_desc"
  | "size_desc"
  | "size_asc";

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

export interface ReindexStatus {
  processed: number;
  total: number;
  failed: number;
  done: boolean;
  error: string | null;
  cancelled: boolean;
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

export async function fetchColors(): Promise<string[]> {
  const res = await apiFetch(`${BASE_URL}/colors`);
  if (!res.ok) throw new Error(`fetch colors failed: ${res.status}`);
  return res.json();
}

export async function fetchObjects(): Promise<string[]> {
  const res = await apiFetch(`${BASE_URL}/objects`);
  if (!res.ok) throw new Error(`fetch objects failed: ${res.status}`);
  return res.json();
}

function toAbsolute(results: ImageResult[]): ImageResult[] {
  return results.map((r) => ({ ...r, thumbnail_url: `${BASE_URL}${r.thumbnail_url}` }));
}

export function downloadUrl(imageId: string): string {
  return `${BASE_URL}/download/${imageId}`;
}

export async function search(
  filters: SearchFilters,
  options: SearchOptions = {},
): Promise<SearchResponse> {
  const params = new URLSearchParams();
  if (filters.text) params.set("text", filters.text);
  if (filters.color) params.set("color", filters.color);
  if (filters.object) params.set("object", filters.object);
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
