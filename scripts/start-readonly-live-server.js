// The regular compatibility server defaults to the repository-root .index.
// ImageFind's Python launcher runs from backend/, so this entry point redirects
// the server to the live backend/.index without moving or copying the catalog.
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
];

for (const [before, after] of replacements) {
  if (!source.includes(before)) throw new Error(`Compatibility server marker not found: ${before}`);
  source = source.replace(before, after);
}

new Function("require", "__dirname", "process", source)(require, __dirname, process);
