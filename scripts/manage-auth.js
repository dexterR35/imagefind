const fs = require("node:fs");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const backendDir = path.join(__dirname, "..", "backend");
const pythonRelPath = process.platform === "win32" ? "Scripts/python.exe" : "bin/python";
const candidates = [".venv", "venv", ".venv312", ".venv311", ".venv313", ".venv310"];
const venvDir = candidates.find((name) => fs.existsSync(path.join(backendDir, name, pythonRelPath)));

if (!venvDir) {
  console.error("No backend virtualenv found. Run `npm run setup:backend` first.");
  process.exit(1);
}

const command = process.argv[2] ?? "status";
const python = path.join(backendDir, venvDir, pythonRelPath);
const result = spawnSync(python, ["-m", "app.auth_cli", command], {
  cwd: backendDir,
  stdio: "inherit",
});

if (result.error) console.error(`Authentication command failed: ${result.error.message}`);
process.exit(result.status ?? 1);

