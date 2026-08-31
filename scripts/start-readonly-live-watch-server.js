// Add a lightweight filesystem overlay to the read-only compatibility server.
// Newly saved images appear immediately, while the existing AI-enriched catalog
// continues to come from backend/.index/index.db.
const fs = require("node:fs");
const path = require("node:path");

const serverPath = path.join(__dirname, "start-readonly-server.js");
let source = fs.readFileSync(serverPath, "utf8");
const pathReplacements = [
  [
    'const INDEX_DB = path.join(ROOT, ".index", "index.db");',
    'const INDEX_DB = path.join(ROOT, "backend", ".index", "index.db");',
  ],
  [
    'const AUTH_DB = path.join(ROOT, ".index", "auth.db");',
    'const AUTH_DB = path.join(ROOT, "backend", ".index", "auth.db");',
  ],
];
for (const [before, after] of pathReplacements) {
  if (!source.includes(before)) throw new Error(`Compatibility server marker not found: ${before}`);
  source = source.replace(before, after);
}

const liveOverlay = String.raw`
const LIVE_EXTENSIONS = new Set([".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff"]);
const LIVE_IMAGES = new Map();
const LIVE_SETTINGS = JSON.parse(fs.readFileSync(path.join(ROOT, "backend", ".index", "settings.json"), "utf8"));
const LIVE_SOURCE_DIR = path.resolve(LIVE_SETTINGS.images_dir);

function liveContentType(filePath) {
  return MIME_TYPES[path.extname(filePath).toLowerCase()] || "application/octet-stream";
}

function registerLiveImage(filePath) {
  try {
    const resolved = path.resolve(filePath);
    if (!LIVE_EXTENSIONS.has(path.extname(resolved).toLowerCase())) return;
    const stat = fs.statSync(resolved);
    if (!stat.isFile()) return;
    if (indexDb.prepare("SELECT 1 FROM images WHERE path=? LIMIT 1").get(resolved)) return;
    const id = "live_" + crypto.createHash("sha256").update(resolved.toLowerCase()).digest("hex").slice(0, 32);
    LIVE_IMAGES.set(id, {
      id,
      path: resolved,
      thumbnail_url: "/thumbnail/" + encodeURIComponent(id),
      ocr_text: "",
      colors: [],
      objects: [],
      mtime: stat.mtimeMs / 1000,
      size: stat.size,
      width: 0,
      height: 0,
      format: path.extname(resolved).slice(1).toUpperCase(),
      date_taken: stat.mtimeMs / 1000,
      indexed_at: Date.now() / 1000,
    });
    console.log("Live image detected: " + resolved);
  } catch (error) {
    if (error && error.code !== "ENOENT") console.error("Live image registration failed:", error.message);
  }
}

function matchingLiveImages(url) {
  const text = String(url.searchParams.get("text") || "").trim().toLowerCase();
  const color = url.searchParams.get("color");
  const object = url.searchParams.get("object");
  if (color || object) return [];
  const terms = text.split(/\s+/).filter(Boolean);
  const entries = Array.from(LIVE_IMAGES.values()).filter((entry) => {
    const haystack = (entry.path + " " + path.basename(entry.path)).toLowerCase();
    return terms.every((term) => haystack.includes(term));
  });
  const sort = url.searchParams.get("sort") || "date_desc";
  entries.sort((a, b) => {
    if (sort === "date_asc") return a.date_taken - b.date_taken || a.id.localeCompare(b.id);
    if (sort === "name_asc") return path.basename(a.path).localeCompare(path.basename(b.path));
    if (sort === "name_desc") return path.basename(b.path).localeCompare(path.basename(a.path));
    if (sort === "size_asc") return a.size - b.size || a.id.localeCompare(b.id);
    if (sort === "size_desc") return b.size - a.size || a.id.localeCompare(b.id);
    return b.date_taken - a.date_taken || a.id.localeCompare(b.id);
  });
  return entries;
}

function mergedLiveSearch(url) {
  const live = matchingLiveImages(url);
  const requestedOffset = Math.max(0, Number(url.searchParams.get("offset")) || 0);
  const requestedLimit = Math.min(200, Math.max(1, Number(url.searchParams.get("limit")) || 60));
  const dbUrl = new URL(url);
  dbUrl.searchParams.set("offset", String(Math.max(0, requestedOffset - live.length)));
  dbUrl.searchParams.set("limit", String(requestedLimit));
  const indexed = searchImages(dbUrl);
  const leading = requestedOffset < live.length ? live.slice(requestedOffset, requestedOffset + requestedLimit) : [];
  return {
    results: leading.concat(indexed.results).slice(0, requestedLimit),
    total: indexed.total + live.length,
  };
}

function startLiveWatcher() {
  try {
    const watcher = fs.watch(LIVE_SOURCE_DIR, { recursive: true }, (_eventType, filename) => {
      if (!filename) return;
      const candidate = path.join(LIVE_SOURCE_DIR, String(filename));
      setTimeout(() => registerLiveImage(candidate), 1500);
    });
    watcher.on("error", (error) => console.error("Live image watcher error:", error.message));
    console.log("Live image watcher active: " + LIVE_SOURCE_DIR);
  } catch (error) {
    console.error("Live image watcher unavailable:", error.message);
  }
}
setImmediate(startLiveWatcher);
`;

const stateMarker = "const requestWindows = new Map();";
if (!source.includes(stateMarker)) throw new Error("Compatibility server state marker not found");
source = source.replace(stateMarker, stateMarker + "\n" + liveOverlay);

const searchBefore = 'sendJson(req, res, 200, searchImages(url), { "Cache-Control": "private, no-store" });';
const searchAfter = 'sendJson(req, res, 200, mergedLiveSearch(url), { "Cache-Control": "private, no-store" });';
if (!source.includes(searchBefore)) throw new Error("Compatibility server search marker not found");
source = source.replace(searchBefore, searchAfter);

const thumbnailBefore = String.raw`const row = indexDb.prepare("SELECT thumbnail_path FROM images WHERE id=?").get(pathname.slice("/thumbnail/".length));
    if (!row) sendJson(req, res, 404, { detail: "image not found" });
    else serveFile(req, res, row.thumbnail_path, { contentType: "image/jpeg", cacheControl: "private, max-age=300, stale-while-revalidate=86400" });`;
const thumbnailAfter = String.raw`const imageId = pathname.slice("/thumbnail/".length);
    const live = LIVE_IMAGES.get(imageId);
    const row = live ? null : indexDb.prepare("SELECT thumbnail_path FROM images WHERE id=?").get(imageId);
    if (live) serveFile(req, res, live.path, { contentType: liveContentType(live.path), cacheControl: "private, max-age=30" });
    else if (!row) sendJson(req, res, 404, { detail: "image not found" });
    else serveFile(req, res, path.isAbsolute(row.thumbnail_path) ? row.thumbnail_path : path.join(ROOT, "backend", row.thumbnail_path), { contentType: "image/jpeg", cacheControl: "private, max-age=300, stale-while-revalidate=86400" });`;
if (!source.includes(thumbnailBefore)) throw new Error("Compatibility server thumbnail marker not found");
source = source.replace(thumbnailBefore, thumbnailAfter);

const downloadBefore = String.raw`const row = indexDb.prepare("SELECT path FROM images WHERE id=?").get(pathname.slice("/download/".length));
    if (!row) sendJson(req, res, 404, { detail: "image not found" });
    else serveFile(req, res, row.path, { downloadName: path.basename(row.path), cacheControl: "private, max-age=3600" });`;
const downloadAfter = String.raw`const imageId = pathname.slice("/download/".length);
    const live = LIVE_IMAGES.get(imageId);
    const row = live ? null : indexDb.prepare("SELECT path FROM images WHERE id=?").get(imageId);
    const filePath = live ? live.path : (row ? (path.isAbsolute(row.path) ? row.path : path.join(ROOT, "backend", row.path)) : null);
    if (!filePath) sendJson(req, res, 404, { detail: "image not found" });
    else serveFile(req, res, filePath, { downloadName: path.basename(filePath), cacheControl: "private, max-age=3600" });`;
if (!source.includes(downloadBefore)) throw new Error("Compatibility server download marker not found");
source = source.replace(downloadBefore, downloadAfter);

const similarBefore = 'const results = similarImages(pathname.slice("/search/similar/".length));';
const similarAfter = 'const similarId = pathname.slice("/search/similar/".length); const results = LIVE_IMAGES.has(similarId) ? [] : similarImages(similarId);';
if (!source.includes(similarBefore)) throw new Error("Compatibility server similarity marker not found");
source = source.replace(similarBefore, similarAfter);

new Function("require", "__dirname", "process", source)(require, __dirname, process);
