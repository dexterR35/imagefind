// Wraps `cloudflared tunnel` so both the local URL and the generated public
// tunnel URL are printed together in one clear block, instead of the
// public URL being buried inside cloudflared's own log noise.
//
// `npm run start:tunnel` launches this alongside the backend via
// `concurrently`, and the backend takes 20-30s to come up (importing
// torch/timm for the RAM++ model) before it actually binds its port. It can
// also be run on its own against a backend already started elsewhere.
// cloudflared, on the other hand,
// connects to Cloudflare's edge almost instantly and starts forwarding
// traffic right away - any request that arrives before the backend is
// listening gets forwarded to a closed port, and Cloudflare's edge returns
// 502 Bad Gateway. So the tunnel is only started once the backend is
// actually accepting connections.
const net = require("node:net");
const { spawn } = require("node:child_process");
const readline = require("node:readline");

const port = Number.parseInt(process.env.IMAGEFIND_PORT || "5175", 10);
if (!Number.isInteger(port) || port < 1 || port > 65535) {
  console.error(`Invalid IMAGEFIND_PORT: ${process.env.IMAGEFIND_PORT}`);
  process.exit(1);
}

const host = "127.0.0.1";
const LOCAL_URL = `http://${host}:${port}`;
const TUNNEL_URL_PATTERN = /https:\/\/[a-z0-9-]+\.trycloudflare\.com/;

let shuttingDown = false;
for (const signal of ["SIGINT", "SIGTERM", "SIGHUP"]) {
  process.on(signal, () => {
    if (shuttingDown) return;
    shuttingDown = true;
  });
}

function backendIsUp() {
  return new Promise((resolve) => {
    const socket = net.connect({ host, port });
    socket.once("connect", () => {
      socket.destroy();
      resolve(true);
    });
    socket.once("error", () => {
      socket.destroy();
      resolve(false);
    });
  });
}

async function waitForBackend(timeoutMs = 180000, intervalMs = 500) {
  const deadline = Date.now() + timeoutMs;
  let announced = false;
  do {
    if (shuttingDown) return false;
    if (await backendIsUp()) return true;
    if (!announced) {
      console.log(`[tunnel] Waiting for backend at ${LOCAL_URL} before starting tunnel...`);
      announced = true;
    }
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  } while (Date.now() < deadline);
  return false;
}

function startTunnel() {
  const cloudflared = spawn("cloudflared", ["tunnel", "--url", LOCAL_URL], {
    stdio: ["ignore", "pipe", "pipe"],
  });

  let printed = false;

  function handleLine(line) {
    process.stderr.write(line + "\n");
    if (printed) return;
    const match = line.match(TUNNEL_URL_PATTERN);
    if (!match) return;
    printed = true;
    console.log("\n[tunnel] Ready:");
    console.log(`[tunnel]   Local:      ${LOCAL_URL}`);
    console.log(`[tunnel]   Cloudflare: ${match[0]}\n`);
  }

  readline.createInterface({ input: cloudflared.stdout }).on("line", handleLine);
  readline.createInterface({ input: cloudflared.stderr }).on("line", handleLine);

  cloudflared.on("error", (err) => {
    console.error(`Failed to start cloudflared: ${err.message}`);
    process.exit(1);
  });

  for (const signal of ["SIGINT", "SIGTERM", "SIGHUP"]) {
    process.on(signal, () => cloudflared.kill(signal));
  }

  cloudflared.on("exit", (code) => {
    process.exit(shuttingDown ? 0 : (code ?? 1));
  });
}

async function main() {
  const up = await waitForBackend();
  if (shuttingDown) {
    process.exit(0);
  }
  if (!up) {
    console.error(
      `[tunnel] Backend never came up at ${LOCAL_URL} after 180s; not starting tunnel.`,
    );
    process.exit(1);
  }
  startTunnel();
}

main();
