# ImageFind — Multi-modal Local Image Search

## Overview

A local mini-app for searching a folder of images (test set: ~5,000 gambling/marketing
assets — icons on transparent backgrounds like clovers/horseshoes, and banners with
heavy text overlays) using multiple signals: semantic meaning, on-image text, color, and
detected objects. No prior tagging or keywording required — everything is derived
automatically at index time.

## Goals

Search images by:

- **Free text / semantic meaning** (CLIP/SigLIP embeddings) — e.g. "casino banner",
  "football celebration"
- **On-image text** (OCR) — e.g. "NetBet", "€400", dates
- **Color** — e.g. "green"
- **Detected objects** — both common real-world objects (person, car — via YOLO) and
  domain-specific icons (clover, horseshoe, coin — via Grounding DINO open-vocabulary
  detection), so an image with a small clover buried among many other elements is still
  found by an explicit object-label match, not just embedding similarity
- **Find Similar** — given an image, find visually/semantically similar ones via
  embedding nearest-neighbor

## Non-goals (v1)

- NAS mounting / remote storage access — points at a local folder path; swapping to a
  NAS path later requires no code changes
- Multi-user auth, hosting, always-on deployment
- Pixel-precise segmentation (SAM) — bounding boxes from Grounding DINO are enough,
  masks add compute cost with no benefit for search/filtering
- A database server (e.g. LanceDB, Postgres/pgvector) — at ~5k images, brute-force
  in-memory search is fast enough; revisit only if the library grows to hundreds of
  thousands of images

## Architecture

Two processes during development:

- **Backend**: Python + FastAPI, run via Uvicorn
- **Frontend**: React + Vite, calls the backend over REST/JSON on localhost

## Indexing pipeline

Triggered manually via `POST /reindex` (a button in the UI), runs as a background task
so the request returns immediately and the UI polls for progress.

For each image file under a configured folder (`IMAGES_DIR`, default `./images`):

1. Skip if already indexed with unchanged file size + mtime (so re-running after adding
   a handful of new images doesn't reprocess the whole folder).
2. Generate and cache a thumbnail (e.g. 320px longest edge, `.index/thumbnails/<id>.jpg`).
3. OCR via EasyOCR → raw extracted text string.
4. Semantic embedding via `open_clip` (CLIP or SigLIP checkpoint) → float32 vector.
5. Dominant color extraction: k-means (k=3–5) in HSV over non-transparent pixels only
   (alpha-aware, so transparent PNG backgrounds don't get counted as a color) → mapped
   to a small fixed set of named buckets (red, orange, yellow, green, blue, purple,
   pink, brown, black, white, gray, gold).
6. Object detection, results unioned into one deduplicated `objects` list per image:
   - **YOLOv8** (COCO 80 classes) for standard real-world objects (person, car, trophy,
     sports ball, ...).
   - **Grounding DINO**, given a configurable text vocabulary list (e.g. clover,
     horseshoe, pot of gold, coin, dice, hat, logo, trophy — user-editable in config
     without code changes) for domain-specific/brand icons that aren't COCO classes.
7. Write/update the corresponding entry in `index.json` and row in `embeddings.npy`.

A single corrupt or unreadable image logs a warning and is skipped — it never aborts
the batch.

## Storage

No database. Two flat files living next to the images folder, under `.index/`:

- `index.json` — array of
  `{id, path, thumbnail_path, ocr_text, colors: [...], objects: [...], mtime, size}`
- `embeddings.npy` — numpy float32 array; row order matches `index.json` order, one row
  per image

Both are loaded fully into memory on backend startup (trivial at this scale — a few
thousand rows of a few-hundred-dim vector). Reindex updates both files and the
in-memory copies atomically (write to temp file, then rename).

## Search API

- `GET /search?text=&color=&object=` — three independent, combinable filters, ANDed
  together when more than one is given:
  - `text`: substring/fuzzy match against `ocr_text`, OR ranked by cosine similarity
    between the query's CLIP text embedding (computed at request time) and each image's
    embedding
  - `color`: exact match against an image's color bucket list
  - `object`: exact match against an image's object label list
  - Results ranked by semantic similarity score among the matching set
- `GET /search/similar/{id}` — nearest neighbors (cosine similarity) to a given image's
  embedding, excluding itself
- `POST /reindex` — starts the indexing pipeline as a background job, returns a job id
- `GET /reindex/status/{job_id}` — poll `{processed, total, done}`
- `GET /thumbnail/{id}` — serves the cached thumbnail
- `GET /colors` and `GET /objects` — distinct values currently present in the index,
  used to populate the color-swatch and object-chip filter UI

## Frontend

Single page:

- Three separate, independently-clearable filter controls: text search box, color
  swatch picker, object chip/dropdown — combine via AND
- Reindex button with a progress indicator (polls job status)
- Results: responsive grid of image cards (thumbnail + filename) from `/search`
- Clicking a card opens the full image with a "Find Similar" action that re-queries
  `/search/similar/{id}`

## Error handling

- Backend: per-image failures during indexing are caught, logged, and skipped —
  never abort the batch
- Frontend: failed search/reindex requests show an inline error state, not a crash

## Testing

- Unit tests: color bucket mapping (HSV ranges → names), file-change-skip logic
  (mtime/size comparison), cosine-similarity ranking function
- Manual/integration: run a full reindex against the real ~5k test image folder, then
  verify representative queries return expected results — a known clover image, a known
  green image, a known "NetBet" text banner, a known photo with a person

## Configuration

- `IMAGES_DIR` — path to the folder being indexed
- Grounding DINO vocabulary list — plain editable list, not hardcoded, so new terms can
  be added without touching code
