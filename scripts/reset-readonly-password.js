const crypto = require("node:crypto");
const path = require("node:path");
const { DatabaseSync } = require("node:sqlite");

const authDbPath = path.resolve(__dirname, "..", "backend", ".index", "auth.db");
const chunks = [];

process.stdin.on("data", (chunk) => chunks.push(chunk));
process.stdin.on("end", () => {
  const password = Buffer.concat(chunks).toString("utf8").replace(/[\r\n]+$/, "");
  if (password.length < 12) throw new Error("password must be at least 12 characters");
  if (Buffer.byteLength(password, "utf8") > 1024) throw new Error("password is too long");

  const memory = 65536;
  const passes = 3;
  const parallelism = 4;
  const salt = crypto.randomBytes(16);
  const hash = crypto.argon2Sync("argon2id", {
    message: Buffer.from(password, "utf8"),
    nonce: salt,
    parallelism,
    tagLength: 32,
    memory,
    passes,
  });
  const unpaddedBase64 = (buffer) => buffer.toString("base64").replace(/=+$/, "");
  const encoded = `$argon2id$v=19$m=${memory},t=${passes},p=${parallelism}$${unpaddedBase64(salt)}$${unpaddedBase64(hash)}`;
  const now = Math.floor(Date.now() / 1000);

  const db = new DatabaseSync(authDbPath);
  db.exec("PRAGMA busy_timeout=5000; BEGIN IMMEDIATE");
  try {
    const row = db.prepare("SELECT password_version FROM auth_config WHERE id=1").get();
    const version = Number(row?.password_version || 0) + 1;
    db.prepare("INSERT INTO auth_config(id,password_hash,password_version,updated_at) VALUES(1,?,?,?) ON CONFLICT(id) DO UPDATE SET password_hash=excluded.password_hash,password_version=excluded.password_version,updated_at=excluded.updated_at").run(encoded, version, now);
    db.exec("DELETE FROM auth_sessions; COMMIT");
    console.log("Shared password updated and persistent sessions revoked.");
  } catch (error) {
    db.exec("ROLLBACK");
    throw error;
  } finally {
    db.close();
  }
});
