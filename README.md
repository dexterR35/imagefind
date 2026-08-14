# ImageFind

Point it at a folder of images — local or a mapped network drive — and it automatically
tags, reads, and color-analyzes every image so you can search your image library like
a search engine instead of scrolling through folders.

```
D:\images\...   →   reindex   →   tags + OCR text + colors + embedding   →   search
```

## What it does

- **Automatic tagging** — no manual keywording. Every image gets tagged with what's
  actually in it (objects, scenes, materials — hundreds of possible tags).
- **Reads text in images** — logos, signs, watermarks, banner copy, anything printed
  on the image becomes searchable.
- **Detects dominant colors** — filter by "green", "gold", "blue", etc.
- **Find Similar** — click any image to pull up other images that look like it.
- **Search** by typed text (matches OCR text + tags), by color, by object tag, or any
  combination of the three.

## How it works

### Indexing

Hitting **Reindex** walks the configured image folder recursively (`.png`, `.jpg`,
`.jpeg`, `.webp`, `.bmp` only) and, for every image that's new or changed since the
last run, does five things:

1. **Thumbnail** — a small cached copy for fast grid display.
2. **Colors** — the image is clustered into a handful of dominant color groups
   (K-means), each mapped to a plain color name (red, gold, blue, ...).
3. **OCR** — any text printed in the image is read out.
4. **Tags** — the image is run through an automatic tagging model, plus an optional
   custom-tag pass (see [Models](#the-models)).
5. **Embedding** — a single vector that captures the image's visual "meaning",
   stored for the similarity search below.

Everything is written to a local index (a JSON file + a NumPy array of embeddings) —
no external database. Unchanged files are skipped on the next reindex (compared by
file size + modified time), progress is saved to disk every 50 images so a crash or a
flaky network drive doesn't lose earlier work, and a single bad file just gets
skipped rather than aborting the whole run.

### Search

The search box and the search API are deliberately **not** "AI-fuzzy" — every result
is directly explainable:

- **Text search** matches the typed text against OCR'd text *and* object tags
  (substring match, case-insensitive). Nothing shows up without a literal reason.
- **Color filter** / **Object filter** — exact match against what was detected.
- **Find Similar** is the one place embeddings are used for search: it ranks every
  other indexed image by how close its embedding vector is to the selected image's
  (cosine similarity) and returns the closest matches. This is genuinely fuzzy/visual
  — the only place in the app that is.

## The models

| Model | Used for | Why |
|---|---|---|
| **[RAM++](https://github.com/xinyu1205/recognize-anything)** (Recognize Anything Model) | Automatic tagging | An open-set tagger — no fixed category list. Trained to recognize thousands of everyday objects, materials, and scenes without being told what to look for. |
| **CLIP** (`open_clip`, ViT-B/32) | Embeddings, Find Similar, custom tags | Turns an image (or a word) into a vector that captures its visual meaning. Two vectors that are close together are things that "look alike" to the model — that's what powers Find Similar and custom-tag matching. |
| **EasyOCR** | Reading text in images | Standard scene-text OCR — finds and reads any printed text in the image. |
| **K-means** (scikit-learn) | Dominant colors | Classic color clustering, no ML model — groups pixels into a few dominant clusters and names each by hue. |

Two optional settings tune the tagging step:
- **Object confidence** — how confident RAM++ must be before a tag counts. Blank uses
  the model's own tuned per-tag thresholds (recommended).
- **Custom tags** — extra words to specifically look for on top of RAM++'s automatic
  tags (e.g. a brand name or a specific concept RAM++ doesn't tag on its own),
  matched via CLIP image/text similarity.

## Example

<img src="example/1.png" width="320" alt="Example: a red lucky-symbols pouch with fruit, a gold 7, and coins">

This image was indexed and automatically got:

```
Colors:  red, white, gold
Objects: alphabet, bag, banana, bowl, cherry, clip art, clover, coin, diamond,
         fruit, gold, grape, number, pot of gold, sack
```

Nobody typed any of those words in — RAM++ produced the object tags and the color
detector produced the color names, purely from the pixels. Typing **"clover"** into
search now finds this image, because that word appears in its tag list.

## Running it

```
npm start
```

runs the backend (FastAPI, port 8000) and frontend (Vite, port 5173) together. Key
environment variables (set before starting the backend):

| Variable | Default | Purpose |
|---|---|---|
| `IMAGES_DIR` | `./images` | Folder to index (local path or a mapped drive, e.g. `Z:\Photos`) |
| `INDEX_DIR` | `./.index` | Where the index/thumbnails/embeddings are stored |
| `RAM_CHECKPOINT_PATH` | `pretrained/ram_plus_swin_large_14m.pth` | RAM++ model weights (~2.9GB, downloaded once, not included in the repo) |

RAM++'s package source is vendored locally in `backend/vendor/recognize-anything`
(it isn't published on PyPI), so a normal `pip install -r requirements.txt` needs no
git or network access — only the checkpoint above is a one-time download.

## Project layout

```
backend/app/
  main.py        FastAPI routes
  indexer.py      reindex pipeline (orchestrates everything below)
  objects.py      RAM++ tagging + CLIP custom-tag matching
  embeddings.py   CLIP image/text embeddings + cosine similarity
  ocr.py          EasyOCR text extraction
  colors.py       K-means dominant color extraction
  search.py       text/color/object filtering + Find Similar ranking
  storage.py      local JSON + NumPy index

frontend/src/
  App.tsx             top-level layout
  SearchFilters.tsx   search box, color swatches, object dropdown
  Settings.tsx        RAM confidence + custom tags
  ImageGrid.tsx / ImageCard.tsx / ImageModal.tsx   results grid + detail view
```
