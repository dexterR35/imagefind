const crypto = require("node:crypto");
const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");
const { DatabaseSync } = require("node:sqlite");

const ROOT = path.resolve(__dirname, "..");
const DIST_DIR = path.join(ROOT, "frontend", "dist");
const INDEX_DB = path.join(ROOT, ".index", "index.db");
const AUTH_DB = path.join(ROOT, ".index", "auth.db");
const HOST = process.env.IMAGEFIND_HOST || "127.0.0.1";
const PORT = Number(process.env.IMAGEFIND_PORT || 8787);
const SESSION_TTL_SECONDS = 7 * 24 * 60 * 60;
const SESSION_COOKIE = "imagefind_session";
const SELECT_COLUMNS = [
  "id", "path", "thumbnail_path", "ocr_text", "colors", "objects", "mtime", "size",
  "width", "height", "format", "date_taken", "indexed_at",
].map((column) => `images.${column}`).join(", ");
const SORTS = {
  date_desc: "images.date_taken DESC",
  date_asc: "images.date_taken ASC",
  name_asc: "images.filename COLLATE NOCASE ASC",
  name_desc: "images.filename COLLATE NOCASE DESC",
  size_desc: "images.size DESC",
  size_asc: "images.size ASC",
};
const MIME_TYPES = {
  ".css": "text/css; charset=utf-8", ".gif": "image/gif",
  ".html": "text/html; charset=utf-8", ".ico": "image/x-icon",
  ".jpeg": "image/jpeg", ".jpg": "image/jpeg",
  ".js": "text/javascript; charset=utf-8", ".json": "application/json; charset=utf-8",
  ".png": "image/png", ".svg": "image/svg+xml", ".webp": "image/webp",
};

if (!Number.isInteger(PORT) || PORT < 1 || PORT > 65535) throw new Error("Invalid IMAGEFIND_PORT");
const indexDb = new DatabaseSync(INDEX_DB, { readOnly: true });
const authDb = new DatabaseSync(AUTH_DB, { readOnly: true });
indexDb.exec("PRAGMA query_only=ON; PRAGMA busy_timeout=5000");
authDb.exec("PRAGMA query_only=ON; PRAGMA busy_timeout=5000");
const sessions = new Map();
const loginAttempts = new Map();
const globalLoginAttempts = [];
const requestWindows = new Map();

function isSecure(req) {
  return String(req.headers["x-forwarded-proto"] || "").split(",", 1)[0].trim() === "https"
    || String(req.headers["cf-visitor"] || "").includes('"scheme":"https"');
}

function securityHeaders(req) {
  const headers = {
    "Content-Security-Policy": "default-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
    "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
  };
  if (isSecure(req)) headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains";
  return headers;
}

function sendJson(req, res, status, body, headers = {}) {
  const content = Buffer.from(JSON.stringify(body));
  res.writeHead(status, {
    ...securityHeaders(req), "Cache-Control": "no-store", "Content-Length": content.length,
    "Content-Type": "application/json; charset=utf-8", ...headers,
  });
  res.end(content);
}

function clientIp(req) {
  return String(req.headers["cf-connecting-ip"] || req.socket.remoteAddress || "unknown").slice(0, 128);
}

function cookies(req) {
  return Object.fromEntries(String(req.headers.cookie || "").split(";").map((part) => {
    const at = part.indexOf("=");
    return at < 0 ? [part.trim(), ""] : [part.slice(0, at).trim(), decodeURIComponent(part.slice(at + 1).trim())];
  }).filter(([name]) => name));
}

function currentSession(req) {
  const token = cookies(req)[SESSION_COOKIE];
  const session = token ? sessions.get(token) : null;
  if (!session) return null;
  if (session.expiresAt <= Math.floor(Date.now() / 1000)) {
    sessions.delete(token);
    return null;
  }
  return { token, ...session };
}

function configuredAuth() {
  return authDb.prepare("SELECT password_hash, password_version FROM auth_config WHERE id=1").get();
}

function verifyArgon2Phc(password, encoded) {
  try {
    const parts = String(encoded).split("$");
    if (parts.length !== 6 || parts[1] !== "argon2id" || parts[2] !== "v=19") return false;
    const parameters = Object.fromEntries(parts[3].split(",").map((item) => {
      const [key, value] = item.split("=");
      return [key, Number(value)];
    }));
    const salt = Buffer.from(parts[4], "base64");
    const expected = Buffer.from(parts[5], "base64");
    const actual = crypto.argon2Sync("argon2id", {
      message: Buffer.from(password, "utf8"), nonce: salt, parallelism: parameters.p,
      tagLength: expected.length, memory: parameters.m, passes: parameters.t,
    });
    return actual.length === expected.length && crypto.timingSafeEqual(actual, expected);
  } catch {
    return false;
  }
}

function allowWithin(key, limit, windowMs) {
  const now = Date.now();
  const timestamps = requestWindows.get(key) || [];
  while (timestamps.length && timestamps[0] <= now - windowMs) timestamps.shift();
  if (timestamps.length >= limit) return false;
  timestamps.push(now);
  requestWindows.set(key, timestamps);
  return true;
}

function loginAllowed(ip) {
  const now = Date.now();
  while (globalLoginAttempts.length && globalLoginAttempts[0] <= now - 300000) globalLoginAttempts.shift();
  const perIp = loginAttempts.get(ip) || [];
  while (perIp.length && perIp[0] <= now - 300000) perIp.shift();
  if (globalLoginAttempts.length >= 100 || perIp.length >= 20) return false;
  globalLoginAttempts.push(now);
  perIp.push(now);
  loginAttempts.set(ip, perIp);
  return true;
}

function readJson(req, maxBytes = 2048) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let size = 0;
    req.on("data", (chunk) => {
      size += chunk.length;
      if (size > maxBytes) {
        reject(Object.assign(new Error("request body too large"), { statusCode: 413 }));
        req.destroy();
      } else chunks.push(chunk);
    });
    req.on("end", () => {
      try { resolve(JSON.parse(Buffer.concat(chunks).toString("utf8"))); }
      catch { reject(Object.assign(new Error("invalid JSON"), { statusCode: 400 })); }
    });
    req.on("error", reject);
  });
}

function cleanSearchValue(value, maxLength) {
  if (value == null || value === "") return null;
  if (value.length > maxLength || /[\0\r\n]/.test(value)) {
    throw Object.assign(new Error("invalid search value"), { statusCode: 422 });
  }
  return value;
}

function rowToResult(row) {
  return {
    id: row.id, path: row.path, thumbnail_url: `/thumbnail/${encodeURIComponent(row.id)}`,
    ocr_text: row.ocr_text, colors: JSON.parse(row.colors), objects: JSON.parse(row.objects),
    mtime: row.mtime, size: row.size, width: row.width, height: row.height, format: row.format,
    date_taken: row.date_taken, indexed_at: row.indexed_at,
  };
}

function searchImages(url) {
  const text = cleanSearchValue(url.searchParams.get("text"), 200);
  const color = cleanSearchValue(url.searchParams.get("color"), 64);
  const object = cleanSearchValue(url.searchParams.get("object"), 128);
  const sort = SORTS[url.searchParams.get("sort")] ? url.searchParams.get("sort") : "date_desc";
  const offset = Math.min(10000000, Math.max(0, Number(url.searchParams.get("offset")) || 0));
  const limit = Math.min(200, Math.max(1, Number(url.searchParams.get("limit")) || 60));
  const clauses = [];
  const params = [];
  const terms = text ? text.split(/\s+/).filter((term) => term.length >= 3) : [];
  const useFts = terms.length > 0;
  const join = useFts ? " JOIN image_fts ON image_fts.rowid=images.rowid" : "";
  if (useFts) {
    clauses.push("image_fts MATCH ?");
    params.push(terms.map((term) => `"${term.replaceAll('"', '""')}"`).join(" AND "));
  } else if (text) {
    clauses.push("(instr(lower(images.filename),lower(?))>0 OR instr(lower(images.path),lower(?))>0 OR instr(lower(images.ocr_text),lower(?))>0 OR EXISTS (SELECT 1 FROM image_objects o WHERE o.image_id=images.id AND instr(lower(o.label),lower(?))>0) OR EXISTS (SELECT 1 FROM image_colors c WHERE c.image_id=images.id AND instr(lower(c.color),lower(?))>0))");
    params.push(text, text, text, text, text);
  }
  if (color) {
    clauses.push("EXISTS (SELECT 1 FROM image_colors c WHERE c.image_id=images.id AND c.color=?)");
    params.push(color);
  }
  if (object) {
    clauses.push("EXISTS (SELECT 1 FROM image_objects o WHERE o.image_id=images.id AND o.label=?)");
    params.push(object);
  }
  const where = clauses.length ? ` WHERE ${clauses.join(" AND ")}` : "";
  const order = useFts && sort === "date_desc"
    ? `image_fts.rank, ${SORTS[sort]}, images.id ASC`
    : `${SORTS[sort]}, ${useFts ? "image_fts.rank, " : ""}images.id ASC`;
  const total = Number(indexDb.prepare(`SELECT count(*) AS total FROM images${join}${where}`).get(...params).total);
  const rows = indexDb.prepare(`SELECT ${SELECT_COLUMNS} FROM images${join}${where} ORDER BY ${order} LIMIT ? OFFSET ?`).all(...params, limit, offset);
  return { results: rows.map(rowToResult), total };
}

function floatAt(blob, index) {
  return Buffer.from(blob.buffer, blob.byteOffset, blob.byteLength).readFloatLE(index * 4);
}

function similarImages(imageId, limit = 20) {
  const source = indexDb.prepare("SELECT embedding FROM images WHERE id=?").get(imageId);
  if (!source) return null;
  const rows = indexDb.prepare(`SELECT ${SELECT_COLUMNS}, images.embedding FROM images WHERE images.id<>?`).all(imageId);
  const dimensions = source.embedding.byteLength / 4;
  let sourceNorm = 0;
  for (let i = 0; i < dimensions; i += 1) sourceNorm += floatAt(source.embedding, i) ** 2;
  sourceNorm = Math.sqrt(sourceNorm);
  for (const row of rows) {
    let dot = 0;
    let norm = 0;
    for (let i = 0; i < dimensions; i += 1) {
      const target = floatAt(row.embedding, i);
      dot += floatAt(source.embedding, i) * target;
      norm += target * target;
    }
    row.similarity = sourceNorm && norm ? dot / (sourceNorm * Math.sqrt(norm)) : -1;
  }
  rows.sort((a, b) => b.similarity - a.similarity);
  return rows.slice(0, limit).map(rowToResult);
}

function serveFile(req, res, filePath, options = {}) {
  let stat;
  try {
    stat = fs.statSync(filePath);
    if (!stat.isFile()) throw new Error("not a file");
  } catch {
    sendJson(req, res, 404, { detail: "file not found" });
    return;
  }
  const headers = {
    ...securityHeaders(req), "Cache-Control": options.cacheControl || "no-cache",
    "Content-Length": stat.size,
    "Content-Type": options.contentType || MIME_TYPES[path.extname(filePath).toLowerCase()] || "application/octet-stream",
  };
  if (options.downloadName) {
    const safeName = options.downloadName.replace(/[\r\n"\\/]/g, "_");
    headers["Content-Disposition"] = `attachment; filename="${safeName}"`;
  }
  res.writeHead(200, headers);
  if (req.method === "HEAD") res.end();
  else fs.createReadStream(filePath).pipe(res);
}

function requireAuth(req, res) {
  const session = currentSession(req);
  if (!session) {
    sendJson(req, res, 401, { detail: "authentication required" }, { "WWW-Authenticate": "Session" });
    return null;
  }
  if (["POST", "PUT", "PATCH", "DELETE"].includes(req.method)) {
    const supplied = String(req.headers["x-csrf-token"] || "");
    const expected = session.csrfToken;
    if (!supplied || supplied.length !== expected.length || !crypto.timingSafeEqual(Buffer.from(supplied), Buffer.from(expected))) {
      sendJson(req, res, 403, { detail: "invalid CSRF token" });
      return null;
    }
  }
  return session;
}

async function handle(req, res) {
  const url = new URL(req.url, `http://${req.headers.host || "localhost"}`);
  const pathname = decodeURIComponent(url.pathname);
  if (req.method === "GET" && pathname === "/health") {
    sendJson(req, res, 200, { status: "ok", mode: "read-only-node" });
    return;
  }
  if (req.method === "GET" && pathname === "/auth/session") {
    const session = currentSession(req);
    sendJson(req, res, 200, session ? {
      authenticated: true, configured: true, expires_at: session.expiresAt, csrf_token: session.csrfToken,
    } : { authenticated: false, configured: Boolean(configuredAuth()) });
    return;
  }
  if (req.method === "POST" && pathname === "/auth/login") {
    const auth = configuredAuth();
    if (!auth) return sendJson(req, res, 503, { detail: "authentication is not configured" });
    const ip = clientIp(req);
    if (!loginAllowed(ip)) return sendJson(req, res, 429, { detail: "too many login attempts" }, { "Retry-After": "300" });
    const body = await readJson(req);
    const password = typeof body.password === "string" ? body.password : "";
    if (!password || Buffer.byteLength(password, "utf8") > 1024 || !verifyArgon2Phc(password, auth.password_hash)) {
      return sendJson(req, res, 401, { detail: "incorrect password" });
    }
    const token = crypto.randomBytes(32).toString("base64url");
    const csrfToken = crypto.randomBytes(32).toString("base64url");
    const expiresAt = Math.floor(Date.now() / 1000) + SESSION_TTL_SECONDS;
    sessions.set(token, { csrfToken, expiresAt, passwordVersion: auth.password_version });
    sendJson(req, res, 200, {
      authenticated: true, configured: true, expires_at: expiresAt, csrf_token: csrfToken,
    }, {
      "Set-Cookie": `${SESSION_COOKIE}=${encodeURIComponent(token)}; Max-Age=${SESSION_TTL_SECONDS}; Path=/; HttpOnly; SameSite=Strict${isSecure(req) ? "; Secure" : ""}`,
    });
    return;
  }

  const publicFrontend = req.method === "GET" && (pathname === "/" || pathname === "/index.html" || pathname === "/favicon.svg" || pathname.startsWith("/assets/"));
  let session = null;
  if (!publicFrontend) {
    session = requireAuth(req, res);
    if (!session) return;
  }
  if (req.method === "POST" && pathname === "/auth/logout") {
    sessions.delete(session.token);
    sendJson(req, res, 200, { status: "logged out" }, {
      "Set-Cookie": `${SESSION_COOKIE}=; Max-Age=0; Path=/; HttpOnly; SameSite=Strict${isSecure(req) ? "; Secure" : ""}`,
    });
    return;
  }
  if (req.method === "GET" && pathname === "/search") {
    if (!allowWithin(`search:${clientIp(req)}`, 30, 10000)) return sendJson(req, res, 429, { detail: "too many search requests" }, { "Retry-After": "10" });
    sendJson(req, res, 200, searchImages(url), { "Cache-Control": "private, no-store" });
    return;
  }
  if (req.method === "GET" && pathname.startsWith("/search/similar/")) {
    const results = similarImages(pathname.slice("/search/similar/".length));
    sendJson(req, res, results ? 200 : 404, results || { detail: "image not found" });
    return;
  }
  if (req.method === "GET" && pathname === "/colors") {
    sendJson(req, res, 200, indexDb.prepare("SELECT DISTINCT color FROM image_colors ORDER BY color").all().map((row) => row.color));
    return;
  }
  if (req.method === "GET" && pathname === "/objects") {
    sendJson(req, res, 200, indexDb.prepare("SELECT DISTINCT label FROM image_objects ORDER BY label").all().map((row) => row.label));
    return;
  }
  if ((req.method === "GET" || req.method === "HEAD") && pathname.startsWith("/thumbnail/")) {
    const row = indexDb.prepare("SELECT thumbnail_path FROM images WHERE id=?").get(pathname.slice("/thumbnail/".length));
    if (!row) sendJson(req, res, 404, { detail: "image not found" });
    else serveFile(req, res, row.thumbnail_path, { contentType: "image/jpeg", cacheControl: "private, max-age=300, stale-while-revalidate=86400" });
    return;
  }
  if ((req.method === "GET" || req.method === "HEAD") && pathname.startsWith("/download/")) {
    if (!allowWithin(`download:${clientIp(req)}`, 60, 60000)) return sendJson(req, res, 429, { detail: "too many download requests" }, { "Retry-After": "60" });
    const row = indexDb.prepare("SELECT path FROM images WHERE id=?").get(pathname.slice("/download/".length));
    if (!row) sendJson(req, res, 404, { detail: "image not found" });
    else serveFile(req, res, row.path, { downloadName: path.basename(row.path), cacheControl: "private, max-age=3600" });
    return;
  }
  if (["/settings", "/model/status"].includes(pathname) || pathname.startsWith("/reindex") || pathname.startsWith("/model/download")) {
    sendJson(req, res, 403, { detail: "this administration action is available only from the local app" });
    return;
  }
  if (req.method === "GET" || req.method === "HEAD") {
    const relative = pathname === "/" ? "index.html" : pathname.replace(/^\/+/, "");
    const candidate = path.resolve(DIST_DIR, relative);
    if (candidate === DIST_DIR || !candidate.startsWith(`${DIST_DIR}${path.sep}`)) return sendJson(req, res, 404, { detail: "not found" });
    if (fs.existsSync(candidate) && fs.statSync(candidate).isFile()) {
      serveFile(req, res, candidate, { cacheControl: relative.startsWith("assets/") ? "public, max-age=31536000, immutable" : "no-cache" });
    } else serveFile(req, res, path.join(DIST_DIR, "index.html"), { cacheControl: "no-cache" });
    return;
  }
  sendJson(req, res, 404, { detail: "not found" });
}

const server = http.createServer((req, res) => {
  handle(req, res).catch((error) => {
    console.error(error);
    if (!res.headersSent) sendJson(req, res, error.statusCode || 500, { detail: error.message || "internal server error" });
    else res.destroy();
  });
});

server.listen(PORT, HOST, () => {
  console.log(`ImageFind read-only server listening on http://${HOST}:${PORT}`);
  console.log(`Index: ${INDEX_DB}`);
});

function shutdown() {
  server.close(() => {
    indexDb.close();
    authDb.close();
    process.exit(0);
  });
}
process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);
