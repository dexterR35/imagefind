# ImageFind

ImageFind creates a searchable catalog for a local folder or NAS containing
large collections of images. It recognizes objects, reads text, extracts
dominant colors, and creates visual embeddings so images can be found without
remembering their exact location.

The original files stay where they are. ImageFind stores only metadata,
embeddings, and generated thumbnails in a local SQLite index.

## Features

- Search filenames, folder paths, OCR text, object tags, custom tags, and colors.
- Filter by an exact object or dominant color.
- Find visually similar images with one click.
- Sort by date, filename, or file size.
- Watch the selected folder for added, changed, moved, and deleted images.
- Reconcile the NAS periodically in case a filesystem event was missed.
- Keep completed indexing work when a long reindex is stopped.
- Store the catalog in SQLite with FTS5 full-text and sqlite-vec vector search.

Supported image formats: PNG, JPG/JPEG, WebP, BMP.

## Examples

### Search by recognized object

<img src="example/1.png" width="480" alt="ImageFind showing a fruit and coin illustration with automatically recognized colors and objects">

ImageFind automatically detected colors such as `red`, `white`, and `gold`,
plus objects including `bag`, `cherry`, `clover`, `coin`, `fruit`, and
`pot of gold`. Searching for **clover** finds this image.

### Search text read from an image

<img src="example/2.png" width="480" alt="ImageFind showing OCR text extracted from a promotional storyboard">

EasyOCR extracted the promotional copy from this storyboard. Searching for
**NETBET**, **storyboard**, or another visible word finds the image even when
the filename does not contain that word.

Other useful searches include:

- `Christmas Banner` — filename match
- `Welcome Pack` — folder-path match
- `clover` — RAM++ object match
- `green` — dominant-color match
- `NETBET BONUS` — OCR text match

## Requirements

- Python 3.10 or newer
- Node.js 20.19 or newer
- Enough disk space for the local SQLite index and thumbnails
- Read access to the image folder or mapped NAS drive
- Optional: NVIDIA GPU for much faster initial indexing

## Install

Clone or download the project, open a terminal in its root directory, then run:

```powershell
npm install
npm --prefix frontend install
npm run setup:backend
```

For an NVIDIA GPU, install the CUDA-enabled PyTorch build instead:

```powershell
npm run setup:backend -- --cuda
```

The setup script creates `backend/.venv` and installs all Python dependencies.

## Start

```powershell
npm start
```

Open:

- Frontend: <http://localhost:5173>
- Backend API: <http://localhost:8000>
- API documentation: <http://localhost:8000/docs>

## First-time setup

1. Open **Settings**.
2. Enter the image folder, for example `Z:\Photos` or
   `Z:\##Work\NETBET`.
3. Select **Install RAM++ Model** if the model is not installed yet.
4. Select **Save & Reindex**.
5. Leave the backend running while the first catalog is created.

The first run can take a long time for hundreds of thousands of images. The
index is saved regularly. If indexing is stopped, already completed images are
kept; starting a normal **Reindex** later skips unchanged completed files.

Changing model or tagging settings with **Save & Reindex** intentionally forces
all images to be processed again.

## Automatic folder updates

The realtime watcher is enabled by default:

- New image: index it.
- Changed image: update its metadata and embedding.
- Moved or renamed image: remove the old path and index the new path.
- Deleted image or folder: remove its database row and cached thumbnail.

A full reconciliation scan runs every four hours by default to catch changes
missed by NAS/SMB notifications. It processes only new or changed files.
Scheduled reconciliation confirms a missing path in two consecutive scans
before deleting it, protecting the index during a temporary NAS interruption.

## Searching

The main search box searches all of these fields together:

- Filename and extension
- Full folder path
- OCR text
- RAM++ objects
- Custom tags
- Dominant colors

The object and color controls are exact filters. Text, object, and color filters
combine with **AND**, so searching `bonus` with object `person` and color `red`
returns images matching all three conditions.

**Find Similar** is different: it uses the selected image's CLIP embedding to
find visually related images. Normal text search deliberately does not use
semantic CLIP matching, which avoids unrelated visual guesses in text results.

Search input is debounced and stale browser requests are cancelled. The API
also validates lengths and control characters, uses parameterized SQLite
queries, treats FTS special characters literally, and limits each client to 30
search requests per 10 seconds.

## Models and tools

| Component | Purpose |
|---|---|
| RAM++ with Swin-L | Automatic object and scene tags |
| OpenCLIP ViT-B/32 (`openai`) | 512-dimensional image embeddings, Find Similar, and custom-tag matching |
| EasyOCR | Text extraction from image pixels |
| K-means | Dominant-color extraction |
| SQLite FTS5 | Fast filename, path, OCR, tag, and color text search |
| sqlite-vec | Cosine nearest-neighbor search over CLIP embeddings |
| Watchdog | Realtime local/NAS filesystem events |

The RAM++ checkpoint is downloaded from Hugging Face by the Settings panel and
stored at `backend/pretrained/ram_plus_swin_large_14m.pth`. OpenCLIP and EasyOCR
download their required model files on first use.

## Local data

Generated data is stored under `backend/.index/`:

- `index.db` — SQLite catalog, FTS rows, and vector embeddings
- `thumbnails/` — generated preview images
- `settings.json` — selected folder and RAM++ settings

This directory is ignored by Git. Back it up if preserving a completed index is
important; it can also be regenerated from the original images.

## Useful environment variables

| Variable | Default | Purpose |
|---|---|---|
| `IMAGES_DIR` | `./images` | Initial image folder before a saved Settings value exists |
| `INDEX_DIR` | `./.index` | Index and thumbnail directory, relative to `backend/` |
| `ENABLE_WATCHER` | `true` | Enable realtime filesystem monitoring |
| `RECONCILE_INTERVAL_SECONDS` | `14400` | Scheduled reconciliation interval |
| `RAM_CHECKPOINT_PATH` | `pretrained/ram_plus_swin_large_14m.pth` | RAM++ checkpoint location |
| `CORS_ALLOWED_ORIGINS` | `http://localhost:5173` | Comma-separated allowed frontend origins |
| `SEARCH_RATE_LIMIT_REQUESTS` | `30` | Search requests allowed per client/window |
| `SEARCH_RATE_LIMIT_WINDOW_SECONDS` | `10` | Search rate-limit window |
| `VITE_API_BASE_URL` | `/api` | Backend URL used by the frontend; `/api` is proxied to the local backend in development |

## Tests

```powershell
cd backend
.venv\Scripts\python.exe -m pytest

cd ..\frontend
npm test
npm run lint
npm run build
```

## Remote access

ImageFind currently has no user login. Do not expose the backend directly to the
public internet. The frontend uses a same-origin `/api` proxy, so a tunnel to the
frontend can carry both UI and API traffic through one URL. Prefer a private VPN
or an authenticated tunnel such as Cloudflare Tunnel with Access for ongoing use.
