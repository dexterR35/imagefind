# ImageFind — Running & Commands

How `npm start` is wired, every command you'll use, and the current state of
this machine. For what the app does and how to search it, see
[`GUIDE.md`](GUIDE.md).

---

## Environment status (snapshot: 2026-08-30)

| Check | Result | Action if not ready |
|---|---|---|
| Backend starts, serves API + built frontend on `http://127.0.0.1:8000` | OK — `/health` 200, `/` 200 | — |
| Python venv + backend deps | OK (`.venv`, Python 3.12) | `npm run setup:backend` |
| `concurrently` (root dep) | OK (9.x) | `npm install` |
| Frontend build present | OK (`prestart` rebuilds anyway) | `npm run build:frontend` |
| Port 8000 | free | `pkill -f "uvicorn app.main:app"` |
| **`cloudflared` installed** | **NO** | install it (see below) |
| **Shared password configured** | **NO** (`{"configured":false}`) | `npm run auth:set-password` |

**Effect of the two "NO" rows:**

1. **No `cloudflared`** — `npm start` launches backend + tunnel together under
   `concurrently --kill-others`, so the tunnel process failing to start **kills
   the backend with it**. Use the local-only run until `cloudflared` is
   installed.
2. **No password** — the app loads but every data route is locked and the login
   page only shows setup instructions until `auth:set-password` is run.

---

## What `npm start` does

```
npm start
├─ prestart          → npm run build:frontend         (vite build → frontend/dist/)
└─ start             → concurrently --kill-others -n backend,tunnel
   ├─ start:backend  → node scripts/start-backend.js
   │                   └─ backend/.venv/bin/python -m uvicorn app.main:app \
   │                        --host 127.0.0.1 --port 8000 \
   │                        --proxy-headers --forwarded-allow-ips 127.0.0.1 \
   │                        --no-server-header
   └─ start:tunnel   → cloudflared tunnel --url http://127.0.0.1:8000
```

- `--kill-others` — if **either** process exits, the other is killed too.
- `Ctrl+C` once stops both.
- Uvicorn is bound to `127.0.0.1` only; nothing listens on the LAN. The public
  URL is whatever `cloudflared` prints (`https://<random>.trycloudflare.com`),
  new every run.

### uvicorn flags (set in `scripts/start-backend.js`)

| Flag | Meaning |
|---|---|
| `--host 127.0.0.1 --port 8000` | localhost only, fixed port |
| `--proxy-headers` | trust `X-Forwarded-*` from the tunnel |
| `--forwarded-allow-ips 127.0.0.1` | ...but only when they come from loopback (the tunnel connects locally) |
| `--no-server-header` | don't advertise the server/version |
| no `--reload` | production mode, single worker |

`start-backend.js` auto-detects the venv (`.venv`, `venv`, `.venv312`, …) and
picks the right `python` per OS.

---

## All commands

### One-time setup

```bash
# Python venv + all backend deps
npm run setup:backend
# GPU box instead (CUDA-enabled torch/torchvision):
npm run setup:backend -- --cuda

# frontend deps
npm --prefix frontend install

# set the shared password  (INTERACTIVE — needs a real terminal)
npm run auth:set-password
```

### Install cloudflared (Linux — pick one)

```bash
# A) .deb package (system-wide, needs sudo)
wget https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
sudo dpkg -i cloudflared-linux-amd64.deb

# B) plain binary, no root
mkdir -p ~/.local/bin
wget -O ~/.local/bin/cloudflared https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64
chmod +x ~/.local/bin/cloudflared
cloudflared --version          # confirm it's on PATH
```

The quick tunnel needs **no Cloudflare account and no login**. It issues a
temporary `trycloudflare.com` URL with no uptime guarantee.

### Run

```bash
# full: build frontend, start backend + public tunnel   (needs cloudflared)
npm start

# LOCAL ONLY: backend + built frontend, no tunnel
npm run build:frontend && npm run start:backend
#   → http://localhost:8000

# just the tunnel, against an already-running backend (separate terminal)
npm run start:tunnel
#   = cloudflared tunnel --url http://127.0.0.1:8000

# frontend dev server only (hot reload on :5173, proxies /api → :8000)
npm run start:frontend
```

### Auth management

```bash
npm run auth:status            # "configured" / "NOT configured"
npm run auth:set-password      # set or rotate (revokes ALL sessions) — interactive
npm run auth:revoke-sessions   # log out every browser, keep the password
```

### Tests / lint / build

```bash
# backend
cd backend && .venv/bin/python -m pytest
cd backend && .venv/bin/python -m pytest tests/test_search.py -q     # one module

# frontend
cd frontend && npm test -- --run     # vitest, once
cd frontend && npm run lint          # oxlint
cd frontend && npm run build         # tsc -b && vite build
```

### Health / troubleshooting

```bash
curl -s http://127.0.0.1:8000/health           # {"status":"ok"}
curl -s http://127.0.0.1:8000/auth/session     # {"authenticated":false,"configured":...}
ss -ltnp | grep :8000                          # is the port in use, and by what
pkill -f "uvicorn app.main:app"                # stop a stray backend
```

---

## Shortest path to a working run

```bash
# 1. set a password (you type this — needs a real terminal)
npm run auth:set-password

# 2a. local only, no tunnel:
npm run build:frontend && npm run start:backend
#     → open http://localhost:8000

# 2b. OR: install cloudflared (above), then
npm start
#     → watch the magenta [tunnel] lines for https://<random>.trycloudflare.com
```

### First launch after that

1. Open `http://localhost:8000`, sign in with the shared password.
2. **Settings** → set the image folder (e.g. `Z:\Photos`).
3. **Install RAM++ Model** if not already installed (~3 GB, one time).
4. **Save & Reindex**, leave the backend running.

Settings, reindex, and model install work only from the **local** app, never
through the tunnel.
