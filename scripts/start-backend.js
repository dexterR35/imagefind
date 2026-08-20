// npm's start:backend script used to be a raw cmd.exe one-liner (Windows-only
// syntax, hardcoded D:\images) that failed outright on Linux/macOS. Resolving
// the venv's python executable per-OS here, in Node, works everywhere npm
// itself runs. The image folder no longer needs a default here at all -
// backend/app/config.py already falls back to ./images, and it's meant to be
// set via the Settings panel now (persisted to .index/settings.json).
const fs = require("node:fs");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const backendDir = path.join(__dirname, "..", "backend");
const pythonRelPath = process.platform === "win32" ? "Scripts/python.exe" : "bin/python";

// Different machines/setups have ended up with the venv under different
// names (.venv, venv, .venv312, ...) - rather than hardcoding one and
// silently failing (ENOENT) when it doesn't match, look for whichever one
// actually has a python executable in it.
const venvCandidates = [".venv", "venv", ".venv312", ".venv311", ".venv313", ".venv310"];
const venvDir = venvCandidates.find((name) =>
  fs.existsSync(path.join(backendDir, name, pythonRelPath)),
);

if (!venvDir) {
  console.error(
    "No Python virtualenv found in backend/ (looked for: " +
      venvCandidates.join(", ") +
      ").\n" +
      "Run `npm run setup:backend` first to create one and install dependencies.",
  );
  process.exit(1);
}

const venvPython = path.join(backendDir, venvDir, pythonRelPath);

const result = spawnSync(venvPython, [
  "-m", "uvicorn", "app.main:app",
  "--host", "127.0.0.1",
  "--port", "8000",
  "--proxy-headers",
  "--forwarded-allow-ips", "127.0.0.1",
  "--no-server-header",
], {
  cwd: backendDir,
  stdio: "inherit",
});

if (result.error) {
  console.error(`Failed to start backend: ${result.error.message}`);
  process.exit(1);
}

process.exit(result.status ?? 1);
