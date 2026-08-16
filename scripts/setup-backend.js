// Creates backend/.venv (if it doesn't already exist) and installs the
// Python dependencies into it. Meant to be run once per machine before
// `npm start` - keeps the venv name/location consistent so
// scripts/start-backend.js can find it without guessing.
const fs = require("node:fs");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const backendDir = path.join(__dirname, "..", "backend");
const venvDir = path.join(backendDir, ".venv");
const pythonRelPath = process.platform === "win32" ? "Scripts/python.exe" : "bin/python";
const venvPython = path.join(venvDir, pythonRelPath);

const useCuda = process.argv.includes("--cuda");

function run(command, args, opts = {}) {
  const result = spawnSync(command, args, { stdio: "inherit", ...opts });
  if (result.error || result.status !== 0) {
    console.error(`\nFailed: ${command} ${args.join(" ")}`);
    process.exit(result.status ?? 1);
  }
}

function findSystemPython() {
  const candidates = process.platform === "win32" ? ["python", "python3"] : ["python3", "python"];
  for (const candidate of candidates) {
    const check = spawnSync(candidate, ["--version"], { stdio: "ignore" });
    if (!check.error && check.status === 0) return candidate;
  }
  console.error(
    "No Python interpreter found on PATH (tried: " + candidates.join(", ") + ").\n" +
      "Install Python 3.10+ and re-run `npm run setup:backend`.",
  );
  process.exit(1);
}

if (!fs.existsSync(venvPython)) {
  const systemPython = findSystemPython();
  console.log(`Creating virtualenv at ${venvDir} using ${systemPython}...`);
  run(systemPython, ["-m", "venv", venvDir]);
} else {
  console.log(`Reusing existing virtualenv at ${venvDir}.`);
}

console.log("Upgrading pip...");
run(venvPython, ["-m", "pip", "install", "--upgrade", "pip"]);

if (useCuda) {
  console.log("Installing CUDA-enabled torch/torchvision (requirements-cuda.txt)...");
  run(venvPython, ["-m", "pip", "install", "-r", "requirements-cuda.txt"], { cwd: backendDir });
}

console.log("Installing backend dependencies (requirements.txt)...");
run(venvPython, ["-m", "pip", "install", "-r", "requirements.txt"], { cwd: backendDir });

console.log("\nBackend setup complete. Run `npm start` to launch the app.");
