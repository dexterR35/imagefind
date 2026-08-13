export interface ImageResult {
  id: string;
  path: string;
  thumbnail_url: string;
  ocr_text: string;
  colors: string[];
  objects: string[];
}

export interface SearchFilters {
  text?: string;
  color?: string;
  object?: string;
}

export interface ReindexStatus {
  processed: number;
  total: number;
  done: boolean;
  error: string | null;
}

const BASE_URL = "http://localhost:8000";

export async function fetchColors(): Promise<string[]> {
  const res = await fetch(`${BASE_URL}/colors`);
  if (!res.ok) throw new Error(`fetch colors failed: ${res.status}`);
  return res.json();
}

export async function fetchObjects(): Promise<string[]> {
  const res = await fetch(`${BASE_URL}/objects`);
  if (!res.ok) throw new Error(`fetch objects failed: ${res.status}`);
  return res.json();
}

function toAbsolute(results: ImageResult[]): ImageResult[] {
  return results.map((r) => ({ ...r, thumbnail_url: `${BASE_URL}${r.thumbnail_url}` }));
}

export async function search(filters: SearchFilters): Promise<ImageResult[]> {
  const params = new URLSearchParams();
  if (filters.text) params.set("text", filters.text);
  if (filters.color) params.set("color", filters.color);
  if (filters.object) params.set("object", filters.object);
  const res = await fetch(`${BASE_URL}/search?${params.toString()}`);
  if (!res.ok) throw new Error(`search failed: ${res.status}`);
  const data: ImageResult[] = await res.json();
  return toAbsolute(data);
}

export async function findSimilar(imageId: string): Promise<ImageResult[]> {
  const res = await fetch(`${BASE_URL}/search/similar/${imageId}`);
  if (!res.ok) throw new Error(`find similar failed: ${res.status}`);
  const data: ImageResult[] = await res.json();
  return toAbsolute(data);
}

export async function startReindex(): Promise<string> {
  const res = await fetch(`${BASE_URL}/reindex`, { method: "POST" });
  if (!res.ok) throw new Error(`start reindex failed: ${res.status}`);
  const data = await res.json();
  return data.job_id;
}

export async function fetchReindexStatus(jobId: string): Promise<ReindexStatus> {
  const res = await fetch(`${BASE_URL}/reindex/status/${jobId}`);
  if (!res.ok) throw new Error(`fetch reindex status failed: ${res.status}`);
  return res.json();
}
