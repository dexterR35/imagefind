// npm's start:backend script used to be a raw cmd.exe one-liner (Windows-only
// syntax, hardcoded D:\images) that failed outright on Linux/macOS. Resolving
// the venv's python executable per-OS here, in Node, works everywhere npm
// itself runs. The image folder no longer needs a default here at all -
// backend/app/config.py already falls back to ./images, and it's meant to be
// set via the Settings panel now (persisted to .index/settings.json).
const { spawnSync } = require("node:child_process");
const path = require("node:path");

const backendDir = path.join(__dirname, "..", "backend");
const venvPython = path.join(
  backendDir,
  ".venv",
  process.platform === "win32" ? "Scripts/python.exe" : "bin/python",
);

const result = spawnSync(venvPython, ["-m", "uvicorn", "app.main:app", "--reload", "--port", "8000"], {
  cwd: backendDir,
  stdio: "inherit",
});

process.exit(result.status ?? 1);
