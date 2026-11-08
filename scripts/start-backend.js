// npm's start:backend script used to be a raw cmd.exe one-liner (Windows-only
// syntax, hardcoded D:\images) that failed outright on Linux/macOS. Resolving
// the venv's python executable per-OS here, in Node, works everywhere npm
// itself runs. The image folder no longer needs a default here at all -
// backend/app/config.py already falls back to ./images, and it's meant to be
// set via the Settings panel now (persisted to .index/settings.json).
const fs = require("node:fs");
const net = require("node:net");
const path = require("node:path");
const { spawn, spawnSync } = require("node:child_process");

const backendDir = path.join(__dirname, "..", "backend");
const pythonRelPath = process.platform === "win32" ? "Scripts/python.exe" : "bin/python";
const host = "127.0.0.1";
const port = Number.parseInt(process.env.IMAGEFIND_PORT || "5175", 10);

if (!Number.isInteger(port) || port < 1 || port > 65535) {
  console.error(`Invalid IMAGEFIND_PORT: ${process.env.IMAGEFIND_PORT}`);
  process.exit(1);
}

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

function listeningPids() {
  if (process.platform === "win32") {
    const result = spawnSync("netstat", ["-ano", "-p", "tcp"], { encoding: "utf8" });
    if (result.error || result.status !== 0) return [];

    const pids = new Set();
    for (const line of result.stdout.split(/\r?\n/)) {
      const columns = line.trim().split(/\s+/);
      if (columns.length < 5 || columns[0].toUpperCase() !== "TCP") continue;
      const localAddress = columns[1];
      const state = columns[3].toUpperCase();
      const pid = Number.parseInt(columns[4], 10);
      if (state === "LISTENING" && localAddress.endsWith(`:${port}`) && pid !== process.pid) {
        pids.add(pid);
      }
    }
    return [...pids];
  }

  const result = spawnSync(
    "lsof",
    ["-nP", `-iTCP:${port}`, "-sTCP:LISTEN", "-t"],
    { encoding: "utf8" },
  );
  if (result.error || (result.status !== 0 && result.status !== 1)) return [];
  return [...new Set(
    result.stdout
      .split(/\s+/)
      .map((value) => Number.parseInt(value, 10))
      .filter((pid) => Number.isInteger(pid) && pid !== process.pid),
  )];
}

function terminateProcessTree(pid) {
  console.log(`Port ${port} is already in use by PID ${pid}; stopping it...`);
  if (process.platform === "win32") {
    const result = spawnSync("taskkill", ["/PID", String(pid), "/T", "/F"], { stdio: "inherit" });
    return result.status === 0;
  }

  try {
    process.kill(pid, "SIGTERM");
    return true;
  } catch (error) {
    console.error(`Could not stop PID ${pid}: ${error.message}`);
    return false;
  }
}

function portIsAvailable() {
  return new Promise((resolve) => {
    const probe = net.createServer();
    probe.unref();
    probe.once("error", () => resolve(false));
    probe.listen({ host, port }, () => probe.close(() => resolve(true)));
  });
}

async function waitForAvailablePort(timeoutMs = 5000) {
  const deadline = Date.now() + timeoutMs;
  do {
    if (await portIsAvailable()) return true;
    await new Promise((resolve) => setTimeout(resolve, 100));
  } while (Date.now() < deadline);
  return false;
}

async function main() {
  const owners = listeningPids();
  if (owners.length > 0) {
    const stopped = owners.every(terminateProcessTree);
    if (!stopped || !(await waitForAvailablePort())) {
      console.error(`Could not free ${host}:${port}. Stop its process manually and try again.`);
      process.exit(1);
    }
  } else if (!(await portIsAvailable())) {
    console.error(
      `${host}:${port} is in use, but its owner could not be identified. ` +
      "Stop that process manually and try again.",
    );
    process.exit(1);
  }

  const backend = spawn(venvPython, [
    "-m", "uvicorn", "app.main:app",
    "--host", host,
    "--port", String(port),
    "--proxy-headers",
    "--forwarded-allow-ips", host,
    "--no-server-header",
  ], {
    cwd: backendDir,
    stdio: "inherit",
  });

  let shuttingDown = false;
  let forceKillTimer;

  function stopBackend(signal = "SIGTERM") {
    if (shuttingDown) return;
    shuttingDown = true;

    if (backend.exitCode !== null || backend.signalCode !== null) return;
    if (process.platform === "win32") {
      // Ctrl+C is delivered to every process attached to the console, so give
      // Uvicorn time to run its application shutdown hooks. SIGTERM from
      // concurrently does not always reach grandchildren on Windows; taskkill
      // is therefore retained as a short fallback for that case.
      forceKillTimer = setTimeout(() => {
        if (backend.exitCode === null && backend.signalCode === null) {
          spawnSync("taskkill", ["/PID", String(backend.pid), "/T", "/F"], { stdio: "ignore" });
        }
      }, 5000);
    } else {
      backend.kill(signal);
    }
  }

  for (const signal of ["SIGINT", "SIGTERM", "SIGHUP"]) {
    process.on(signal, () => stopBackend(signal));
  }

  backend.on("error", (error) => {
    console.error(`Failed to start backend: ${error.message}`);
    process.exitCode = 1;
  });

  backend.on("exit", (code, signal) => {
    clearTimeout(forceKillTimer);
    if (!shuttingDown && signal) {
      console.error(`Backend stopped by ${signal}.`);
    }
    process.exit(shuttingDown ? 0 : (code ?? 1));
  });
}

main().catch((error) => {
  console.error(`Failed to start backend: ${error.message}`);
  process.exit(1);
});
