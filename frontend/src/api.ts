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
  return res.json();
}

export async function fetchObjects(): Promise<string[]> {
  const res = await fetch(`${BASE_URL}/objects`);
  return res.json();
}

export async function search(filters: SearchFilters): Promise<ImageResult[]> {
  const params = new URLSearchParams();
  if (filters.text) params.set("text", filters.text);
  if (filters.color) params.set("color", filters.color);
  if (filters.object) params.set("object", filters.object);
  const res = await fetch(`${BASE_URL}/search?${params.toString()}`);
  return res.json();
}

export async function findSimilar(imageId: string): Promise<ImageResult[]> {
  const res = await fetch(`${BASE_URL}/search/similar/${imageId}`);
  return res.json();
}

export async function startReindex(): Promise<string> {
  const res = await fetch(`${BASE_URL}/reindex`, { method: "POST" });
  const data = await res.json();
  return data.job_id;
}

export async function fetchReindexStatus(jobId: string): Promise<ReindexStatus> {
  const res = await fetch(`${BASE_URL}/reindex/status/${jobId}`);
  return res.json();
}
