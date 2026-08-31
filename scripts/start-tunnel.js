// Wraps `cloudflared tunnel` so both the local URL and the generated public
// tunnel URL are printed together in one clear block, instead of the
// public URL being buried inside cloudflared's own log noise.
const { spawn } = require("node:child_process");
const readline = require("node:readline");

const LOCAL_URL = "http://127.0.0.1:5175";
const TUNNEL_URL_PATTERN = /https:\/\/[a-z0-9-]+\.trycloudflare\.com/;

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

cloudflared.on("exit", (code) => {
  process.exit(code ?? 1);
});
