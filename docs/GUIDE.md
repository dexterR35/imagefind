# ImageFind — Guide

A reference for what ImageFind does, what each model is for, and how to search
it. For install and run instructions see the [README](../README.md).

---

## 1. What ImageFind is

ImageFind turns a folder of images (a local disk or a mapped NAS share) into a
**searchable catalog**. It looks at every picture once and records:

- what **objects and scenes** are in it (a model, no manual tagging)
- any **text printed inside** the image (OCR)
- its **dominant colors**
- a **visual fingerprint** (embedding) used for "find images that look like this"
- file facts: name, full path, size, dimensions, format, capture date

All of that goes into a local SQLite database next to the images. **The original
files are never moved, renamed, or modified.** If the database is lost it can be
rebuilt from the images.

It is a **search engine**, not a chat tool. You type words or pick filters and
get back a grid of matching images. There is no language model and no generated
answer — see [Section 9](#9-what-imagefind-is-not).

---

## 2. Who it is for / when to use it

Use it when you have **too many images to remember where anything is** and the
filenames don't help. Typical situations:

| Situation | How ImageFind helps |
|---|---|
| Marketing / creative team with years of campaign exports on a NAS | Find the storyboard by a word on it, or every "green + clover" asset |
| A photo archive with generic names (`IMG_4821.jpg`) | Search by what's *in* the photo — "beach", "dog", "car" |
| Screenshots and design mockups | OCR makes the on-screen text searchable |
| "We made a banner with a coin and a pot of gold last year" | Object tags (`coin`, `pot of gold`) find it without the path |
| You found one good image and want more like it | **Find Similar** returns visually related images |

It is designed for a **small internal group** sharing one password over a
temporary tunnel, not a public service.

---

## 3. How indexing works

For each supported image (`.png`, `.jpg`, `.jpeg`, `.webp`, `.bmp`) the indexer,
running one image at a time:

1. Reads the file **once** and applies EXIF rotation so a sideways phone photo is
   catalogued the way it displays.
2. Writes a ≤320px **thumbnail** (JPEG) into `backend/.index/thumbnails/`.
3. Extracts **dominant colors** with K-means (transparent pixels ignored).
4. Computes the **CLIP embedding** (512 numbers) for similarity search.
5. Runs **OCR** to pull out any printed text.
6. Runs **RAM++** to get object/scene tags; generic tags like `photo` /
   `illustration` / `white background` are dropped
   (`RAM_TAG_DENYLIST`).
7. If you configured **custom tags**, matches each against the image
   (see [Section 8](#8-custom-tags)).
8. Stores one row: metadata + `ocr_text` + `colors` + `objects` (RAM++ tags and
   custom tags combined) + the embedding, plus derived full-text and vector
   indexes.

A re-index skips a file whose size and modification time are unchanged (with a
small tolerance so a NAS that rounds timestamps doesn't re-process everything).
**Save & Reindex** in Settings forces every image through again.

If some images fail (corrupt file, vanished mid-scan, an unreadable subfolder),
the run continues, indexes everything it can, and reports the failures — the
count plus the first 100 paths — in the reindex status. An incomplete folder
scan never triggers deletion of existing entries.

---

## 4. The models — what each is for

| Model | What it produces | Feeds which search | Runs |
|---|---|---|---|
| **RAM++** (Recognize Anything Plus, Swin-L backbone) | Open-vocabulary **object & scene tags** — `cat`, `coin`, `clover`, `beach`, `person`, `pot of gold` … no vocabulary to configure | Main search box (tag text) and the **Object** filter | During indexing only. The ~3 GB checkpoint is installed from Settings and unloaded when indexing is idle. |
| **OpenCLIP ViT-B/32** (`openai` weights) | A **512-d image embedding** per image; also text embeddings on demand | **Find Similar** (image↔image cosine nearest-neighbor) and **custom-tag** matching (image↔text) | Embedding: during indexing. Text side: when a custom tag is evaluated. Model stays loaded. |
| **EasyOCR** (English) | **Text read from the pixels** of the image | Main search box (OCR text) | During indexing only. Model stays loaded. |
| **K-means** (scikit-learn, not a neural net) | Up to `COLOR_CLUSTERS` **dominant colors**, mapped to 12 names: red, orange, yellow, gold, green, blue, purple, pink, brown, black, white, gray | Main search box (color words) and the **Color** filter | During indexing only. |

Supporting infrastructure (not models):

| Component | Role |
|---|---|
| **SQLite FTS5** (trigram tokenizer) | Fast substring/partial text search over filename, path, OCR text, tags, colors; bm25 relevance ranking |
| **sqlite-vec** (`vec0` virtual table) | Cosine nearest-neighbor over the CLIP embeddings, for Find Similar |
| **Watchdog** | Real-time filesystem events (add / change / move / delete) |

### Why CLIP is *not* used for normal text search

Typing `clover` searches your tags and OCR text literally. It does **not** do a
CLIP text→image semantic match, because that tends to surface confident-looking
but wrong guesses (a baseball photo for "clover"). Semantic matching is reserved
for **Find Similar**, which compares a real image you picked.

---

## 5. Searching

### The main search box

One box searches these fields together:

- **Filename** and extension
- **Full folder path**
- **OCR text** (words printed in the image)
- **Object tags** — RAM++ tags *and* your custom tags
- **Dominant colors**

Rules:

| Query shape | Behavior |
|---|---|
| One word, 3+ chars (`clover`) | Trigram match across all fields. Results ranked by **relevance** (bm25) when the sort is left on the default; whole-word hits are floated above matches that only occur *inside* a longer word. |
| Multiple words (`red cat`) | Each word 3+ chars becomes its own term, **AND-ed**: the image must match `red` somewhere **and** `cat` somewhere. Words shorter than 3 chars are ignored (`a red cat` → `red` AND `cat`). |
| 1–2 chars only (`AI`, `3`) | Falls back to a plain case-insensitive substring scan over the same fields (no ranking). |
| FTS punctuation (`"`, `*`, `OR`, `NEAR(`) | Treated as literal text, never as query syntax. |

Input is length-checked (≤200 chars), control characters rejected, and every
query is parameterized — nothing you type can become SQL or FTS syntax. Typing
is debounced in the browser and stale requests are cancelled; the server also
limits each client to 30 searches per 10 seconds.

### The Object and Color filters

- **Object** dropdown = an **exact tag match** (`label = 'cat'`), not a substring.
  Use it when a loose text match for `cat` would also hit `catalog.png` or a
  `.../vacation/` folder.
- **Color** dropdown = an exact match on one of the 12 dominant-color names.
- Text, Object, and Color **combine with AND**. `bonus` + object `person` +
  color `red` returns only images matching all three.

### Sorting

Newest / oldest (capture date), name A–Z / Z–A, largest / smallest file.
Default is newest first — and with a text query on that default, relevance wins
and date is the tie-breaker. Pick any other sort and it takes over, with
relevance as the tie-breaker.

### Find Similar

Open an image, click **Find Similar**. It uses that image's CLIP embedding to
return up to 20 visually related images by cosine distance (itself excluded).
This is a separate view — no text, filters, or pagination apply to it.

---

## 6. Search scenarios — worked examples

Assume a marketing archive on `Z:\Photos`. "→" is what to type / pick.

### A. You remember a word printed on the image

> A storyboard with **NETBET** written on it.

→ Search box: `NETBET` — matched from OCR text. `storyboard` or `BONUS` work
too if those words are visible in the image.

### B. You remember what's *in* the image, not its name

> A banner with a **clover** on it, filename is `banner_final_v3.png`.

→ Search box: `clover` — matched from the RAM++ object tag. The filename never
mattered.

### C. You want an exact object, no false hits

> Every image that actually contains a **cat**, not files named `catalog`.

→ **Object** filter: `cat`. (Search box `cat` would also match `catalog.png`
and `.../vacation/`.)

### D. Object *and* text together

> The image with a **cat** and the number **500** on it.

→ One box: `cat 500` (AND of both terms), **or** Object filter `cat` + search
box `500`. The second is sharper because `cat` is then an exact tag.

### E. Object *and* color together

> A **red** **cat** picture.

→ One box: `red cat`, **or** search box `cat` + Color filter `red`.
Note: this finds *an image that contains a cat and whose palette includes red* —
it does not verify the cat itself is red (a black cat on a red sofa still
matches). There is no per-object color anywhere in the app.

### F. Multiple conditions

> A **bonus** creative with a **person** in it that uses **green**.

→ Search box: `bonus`, Object filter: `person`, Color filter: `green`. All
AND-ed.

### G. Find by folder / campaign

> Everything under the **Welcome Pack** campaign folder.

→ Search box: `Welcome Pack` — matched from the folder path. Both words must
appear (path separators count as spaces, so `Welcome Pack` matches
`.../Welcome Pack/...` but also `.../Welcome/.../Pack/...`).

### H. Color only

> All the **gold** assets.

→ **Color** filter: `gold`. (`gold` is distinguished from `yellow` by
saturation/brightness.) Or type `gold` in the box to also catch "gold" in
filenames and OCR.

### I. Short term

> Anything mentioning **AI**.

→ Search box: `AI` — 2 chars, so it's a substring scan over all fields
(filename, path, OCR, tags, colors). No relevance ranking, but it works.

### J. More like this one

> You found the perfect hero image and want alternates.

→ Open it → **Find Similar**. Returns the 20 closest by visual embedding.
Unlike text search this *is* semantic — lighting, composition, subject.

### K. A named character or brand not in RAM++'s vocabulary

> Images of **Zeus** (a specific mascot).

→ Add `zeus` as a **custom tag** in Settings, optionally drop a few example
images in `backend/reference_tags/zeus/`, then Save & Reindex. Afterwards
`zeus` works in the search box and the Object filter like any other tag.
See [Section 8](#8-custom-tags).

### L. Narrow a big result set

> `logo` returns 4,000 images.

→ Add a color (`logo` + `blue`), an object (`logo` + object `text`), or switch
sort to Largest first to get print-resolution files on top. With the default
sort, the most on-topic `logo` matches are already ranked first.

---

## 7. Keeping the index current

The real-time watcher is on by default:

| Filesystem event | Action |
|---|---|
| New image | Index it (after its size stops changing, to avoid reading a half-copied file) |
| Changed image | Re-index it |
| Moved / renamed | Drop the old path, index the new one |
| Deleted image or folder | Remove the row(s) and cached thumbnail(s) |

Because NAS/SMB change notifications are not guaranteed, a full **reconciliation
scan** runs every 4 hours and processes only new/changed files. A path missing
from the share is only deleted after **two consecutive** reconciliation scans
agree it's gone, so a brief NAS outage won't wipe the index.

---

## 8. Custom tags

Custom tags extend RAM++'s automatic tags with words *you* choose. Each is
matched by **CLIP cosine similarity** between the image embedding and the tag's
text embedding; a match ≥ `RAM_CUSTOM_TAG_THRESHOLD` (default 0.22) adds the tag
to that image. Matched custom tags are stored exactly like RAM++ tags, so they
are searchable in the box and selectable in the Object filter.

For a named entity that a bare word doesn't pin down well (a specific mascot,
character, product), put a few example images in
`backend/reference_tags/<tag>/`. Their embeddings are blended with the text
embedding into a sharper match target. Reference images are re-read on every
reindex, so adding more just needs a Save & Reindex.

Set custom tags in **Settings** (local app only). Tags may not contain path
separators or `..`.

---

## 9. What ImageFind is *not*

- **Not RAG / not a chatbot.** It returns images, never a written answer. There
  is no LLM. If you ever want "ask a question, get a sentence about your
  library," that would be a new layer built *on top of* this search — the
  indexing pipeline is already the retrieval half.
- **No per-object color.** Colors are whole-image. "red cat" = cat present +
  red present, not "a cat that is red."
- **No semantic text search.** `clover` matches the literal tag/word, not
  "things that evoke clovers." Semantic matching is Find Similar only.
- **Trigram substring matching.** A 3+ char term can match inside a longer word
  (`cat` inside `communication`, `500` inside `1500`). Relevance ranking and the
  whole-word boost push true matches up, and the Object/Color filters are exact,
  but the text box itself is substring-based by design (so partial words work).
- **English OCR only.**
- **Formats:** PNG, JPG/JPEG, WebP, BMP. No HEIC/HEIF, TIFF, GIF, AVIF.
