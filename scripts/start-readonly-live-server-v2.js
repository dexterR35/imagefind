// Run the compatibility server against backend/.index and resolve the
// catalog's relative file paths from backend/, matching the Python launcher.
const fs = require("node:fs");
const path = require("node:path");

const serverPath = path.join(__dirname, "start-readonly-server.js");
let source = fs.readFileSync(serverPath, "utf8");
const replacements = [
  [
    'const INDEX_DB = path.join(ROOT, ".index", "index.db");',
    'const INDEX_DB = path.join(ROOT, "backend", ".index", "index.db");',
  ],
  [
    'const AUTH_DB = path.join(ROOT, ".index", "auth.db");',
    'const AUTH_DB = path.join(ROOT, "backend", ".index", "auth.db");',
  ],
  [
    'else serveFile(req, res, row.thumbnail_path, { contentType: "image/jpeg", cacheControl: "private, max-age=300, stale-while-revalidate=86400" });',
    'else serveFile(req, res, path.isAbsolute(row.thumbnail_path) ? row.thumbnail_path : path.join(ROOT, "backend", row.thumbnail_path), { contentType: "image/jpeg", cacheControl: "private, max-age=300, stale-while-revalidate=86400" });',
  ],
  [
    'else serveFile(req, res, row.path, { downloadName: path.basename(row.path), cacheControl: "private, max-age=3600" });',
    'else serveFile(req, res, path.isAbsolute(row.path) ? row.path : path.join(ROOT, "backend", row.path), { downloadName: path.basename(row.path), cacheControl: "private, max-age=3600" });',
  ],
];

for (const [before, after] of replacements) {
  if (!source.includes(before)) throw new Error(`Compatibility server marker not found: ${before}`);
  source = source.replace(before, after);
}

new Function("require", "__dirname", "process", source)(require, __dirname, process);
