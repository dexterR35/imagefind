# SQLite-backed IndexStore + real-time watching for 300k+ image libraries

## Context & Problem

Confirmed deployment model: one server (a PC with CUDA GPU, 64GB RAM, i7) runs
the full backend and does all indexing against a department-shared NAS. Five
users access it purely as browser clients over the LAN — no other machine
runs its own backend or index, and no per-user access restriction is needed
(shared department resource). Users add images/folders to the NAS daily, and
the library goes back 5 years of accumulated assets.

The backend targets image libraries of **300,000+ images minimum** (NAS-hosted).
The current persistence layer (`backend/app/storage.py`) is not built for that scale:

- `IndexStore.save()` serializes the **entire** `self.entries` list to `index.json`
  and rewrites the **entire** `embeddings.npy` file, every time it's called
  (`storage.py:71-79`).
- `Indexer.run_reindex()` calls `self.store.save()` every 50 processed images
  (`indexer.py:115-116`), plus once more at the end. At 300k images that's
  roughly **6,000 full-file rewrites** of an ever-growing JSON file (which
  would reach 100MB+ at this scale) and an ever-growing `.npy` file, over the
  course of one reindex run.
- `search.py`'s `find_similar()` computes similarity with a Python `for` loop —
  one function call and one dot product per entry, sequentially
  (`search.py:52-57`). At 300k entries this is meaningfully slower than a
  single vectorized operation.

The deployment machine has a GPU and a strong CPU, so **model inference
throughput is not the concern here** — this design is scoped to the
persistence layer and the similarity-search loop only.

## Goals

1. Eliminate full-file rewrites on every `save()` — writes must be incremental
   (only the changed row), not "reserialize everything."
2. Eliminate the unbounded single-JSON-blob growth/reserialization pattern.
3. Vectorize `find_similar()` so it's a single matrix operation instead of a
   300k-iteration Python loop.
4. Preserve `IndexStore`'s existing public interface (`all()`, `get()`,
   `get_by_path()`, `get_embedding()`, `upsert()`, `prune()`, `save()`,
   `load()`, `.entries`, `.embeddings`, `.lock`) so `indexer.py`, `search.py`,
   and `main.py` require **zero or near-zero changes**. This is a persistence
   *implementation* change, not an interface change.
5. Migrate any existing `index.json`/`embeddings.npy` data automatically on
   first run — nothing gets silently dropped.
6. New images/folders added to the NAS day-to-day get indexed automatically,
   without a full directory rescan, and without requiring anyone to click
   "Reindex."
7. The other 4 users can reach the server over the LAN from a browser (today
   CORS only allows `localhost:5173`).
8. Users can download the original matched file, not just view a thumbnail.

## Non-goals (for this change)

- Moving tag/color/text search filtering into SQL (`search.py`'s Python-side
  filtering over the in-memory list stays as-is — it's not the bottleneck at
  this scale: simple `in` checks over a few hundred thousand small entries is
  low tens-of-milliseconds, not worth the added schema complexity right now).
- A dedicated ANN/vector index (FAISS or similar). A single vectorized numpy
  query over a 300k × 512 float32 matrix (~600MB) is still low tens of
  milliseconds. Revisit only if the library grows into the millions or needs
  high concurrent query throughput.
- Changing indexing throughput/model inference — out of scope, hardware is
  already adequate per the user.

## Architecture

Replace `index.json` + `embeddings.npy` with a single SQLite database file,
`index.db`, in `INDEX_DIR`. `IndexStore` keeps an in-memory mirror
(`self.entries: list[ImageEntry]`, `self.embeddings: np.ndarray`) exactly as
today, populated from SQLite at `load()` time — so every existing read path
(`all()`, `get()`, `get_by_path()`, `get_embedding()`, and `search.py`'s
in-memory scans) is unchanged and stays fast. SQLite becomes the durable
backing store; the in-memory mirror is what the rest of the app actually
reads from, same as now.

### Schema

```sql
CREATE TABLE IF NOT EXISTS images (
    id TEXT PRIMARY KEY,
    path TEXT UNIQUE NOT NULL,
    thumbnail_path TEXT NOT NULL,
    ocr_text TEXT NOT NULL,
    colors TEXT NOT NULL,      -- JSON-encoded list[str]
    objects TEXT NOT NULL,     -- JSON-encoded list[str]
    mtime REAL NOT NULL,
    size INTEGER NOT NULL,
    embedding BLOB NOT NULL    -- np.ndarray(embedding_dim, float32).tobytes()
);
```

`colors`/`objects` stay JSON-encoded text rather than a normalized join
table — `ImageEntry` doesn't change shape, and nothing downstream needs SQL
to query by tag (see Non-goals). This keeps the migration mechanical.

The connection opens with `PRAGMA journal_mode=WAL` (safe concurrent reads
during a write-heavy reindex, better crash recovery than the default
rollback journal for a long-running writer process).

### Write path (`upsert` / `save`)

`upsert(entry, embedding)`:
1. Update the in-memory mirror exactly as today (O(1) dict-based, no full
   rebuild — unchanged from current code).
2. Execute one `INSERT OR REPLACE INTO images (...) VALUES (...)` against the
   open SQLite connection.

Critically, **this does not commit**. `sqlite3` in Python starts an implicit
transaction on the first write and holds it open until `commit()` is called.
`save()` becomes:

```python
def save(self) -> None:
    with self.lock:
        self._conn.commit()
```

This exactly matches the existing call pattern in `indexer.py` — `save()`
every 50 images, plus once at the end (`indexer.py:115-116`) — with **zero
changes needed in `indexer.py`**. What changes is what `save()` *does*
underneath: committing a batch of already-written rows (cheap: one WAL
checkpoint) instead of reserializing the entire dataset from scratch
(expensive: O(total size), the current bug).

### Delete path (`prune`)

`prune(keep_paths: set[str])` must delete rows whose path isn't in
`keep_paths`. A naive `WHERE path NOT IN (?, ?, ..., ?)` with 300k
placeholders risks hitting SQLite's bound-parameter limit. Instead:

1. `CREATE TEMP TABLE keep_paths (path TEXT PRIMARY KEY)`.
2. Bulk-insert `keep_paths` via `executemany`.
3. `DELETE FROM images WHERE path NOT IN (SELECT path FROM keep_paths)`.
4. Drop the temp table.
5. Rebuild the in-memory mirror (`self.entries`, `self.embeddings`,
   `_by_id`, `_by_path`) same as today's `prune()`.

### Load / migration path (`load`)

On `load()`:
1. If `index.db` doesn't exist yet **and** a legacy `index.json` (+
   `embeddings.npy`) is present in `INDEX_DIR`, run a one-time import: read
   the legacy files exactly as the current code does, insert every row into
   the new `images` table in a single transaction, commit. This is a
   straight port of existing data — same `ImageEntry` fields, same
   embeddings — nothing is recomputed or re-indexed.
2. `SELECT * FROM images`, populate `self.entries`/`self.embeddings` and
   rebuild `_by_id`/`_by_path` — same shape of in-memory state as today's
   `load()` produces, just sourced from SQLite instead of JSON/npy.
3. The legacy `index.json`/`embeddings.npy` files are left in place after a
   successful migration (not deleted) — cheap insurance in case something
   about the migration needs to be inspected or re-run; they're simply never
   read again once `index.db` exists.

## `find_similar()` vectorization (`search.py`)

Replace the per-entry Python loop with a single matrix-vector product. Since
every embedding is already L2-normalized at write time (`embeddings.py:38`,
`:47`), the dot product **is** cosine similarity, so:

```python
sims = store.embeddings @ query_embedding   # shape (n,), one BLAS call
self_index = store._by_id[image_id]
sims[self_index] = -np.inf                  # exclude the query image itself
top = np.argpartition(-sims, limit)[:limit] # O(n) top-k, not a full O(n log n) sort
top = top[np.argsort(-sims[top])]           # order just the top-k
return [entries[i] for i in top]
```

`np.argpartition` avoids a full sort over all 300k similarities when only the
top ~20 are needed — a real difference at this scale versus `argsort` over
the whole array, and no more complex to write.

## Concurrency & crash safety

- WAL mode allows the reindex writer and any concurrent `/search` reader to
  proceed without blocking each other (SQLite's normal WAL behavior).
- A crash between `save()` calls loses at most the last batch (≤50 images,
  same durability window as today) — the difference is recovery is a normal
  SQLite WAL replay, not a truncated/corrupt JSON file.
- `self.lock` (the existing `threading.RLock`) continues to guard all
  read/write access to the in-memory mirror exactly as it does today; SQLite
  access happens under the same lock, so no new concurrency primitive is
  introduced.

## Error handling

- A corrupt/unreadable `index.db` at startup is a hard failure (matches
  today's behavior for a corrupt `index.json`/mismatched embeddings length —
  `storage.py:56-63` currently resets to empty on a length mismatch; the
  SQLite version applies the same "reset to empty rather than crash the
  whole app" policy if the schema is unreadable).
- Migration failure (legacy files present but unreadable) logs a warning and
  starts `index.db` empty, same as today's behavior when `index.json` is
  absent — it does not delete or touch the legacy files.

## Testing plan

Following TDD, each behavior gets a failing test first:

- `test_storage.py`: upsert/get/get_by_path/prune/save/load round-trip
  against a real (tmp_path) SQLite file — same test shapes as today's
  JSON-based tests, retargeted at the new backend.
- A dedicated migration test: seed a tmp dir with a legacy `index.json` +
  `embeddings.npy`, call `load()`, assert the data lands correctly in
  `index.db` and is queryable.
- A `prune()` test with a path count large enough to prove the temp-table
  approach (not a naive `NOT IN (...)` list) is actually being used —
  e.g., patch `sqlite3.Cursor.execute` or just test correctness with a
  representative set; the existing prune test's assertions extend naturally.
- `test_search.py`: `find_similar` test(s) updated only if the return
  ordering/behavior changes in an observable way — the public contract
  (returns entries sorted by similarity, excludes self, respects `limit`)
  stays the same, so most existing tests should pass unmodified; add one
  test asserting correctness against a hand-computed similarity ordering.
- No changes expected to `test_indexer.py` or `test_main.py` — they exercise
  `IndexStore` through its public interface, which is unchanged.

## Real-time folder watching (DAM pattern)

Matches how real DAM/photo-management tools handle network-shared libraries:
watching is the primary mechanism, periodic reconciliation is a backstop, not
the other way around.

- **Watcher**: use the `watchdog` library, which wraps `ReadDirectoryChangesW`
  on Windows (the server's actual OS) and `inotify` on Linux, recursively
  watching `config.IMAGES_DIR`. Runs as a background thread started at app
  startup, stopped at shutdown.
- **On file created/modified**: don't re-walk the directory. Call the
  existing `Indexer.process_image()` for that single path, then
  `store.upsert()`. This is exactly the incremental-write path the SQLite
  design above already sets up cheaply.
- **Debounce before processing**: a file copied onto a NAS share can fire a
  "created" event before the copy finishes, and large images copied over a
  network can take real time to fully land. Before processing a created/
  modified event, wait until the file's size is stable across two checks a
  short interval apart (e.g. 1-2s) rather than processing immediately — reading
  a partially-written file would produce a corrupt thumbnail/embedding.
- **On file deleted**: remove just that one entry. This needs a new
  `IndexStore.delete_by_path(path)` — a single-row `DELETE`, distinct from
  `prune()` (which does a full keep-set reconciliation and is for the
  scheduled scan below, not per-event deletes).
- **Reconciliation (backstop, not primary)**: an infrequent full scan (hours,
  not seconds) reusing the existing `run_reindex()`/`needs_reindex()` skip
  logic, to catch anything the watcher missed — e.g. watcher downtime, or the
  known caveat that SMB change-notifications for files written by *other*
  machines onto the same share aren't 100% guaranteed to fire. This is the
  same job the manual "Reindex" button already does; the reconciliation pass
  is just that, running on a timer instead of only on click.

## CORS / LAN access for multi-user deployment

`main.py:19` currently hardcodes `allow_origins=["http://localhost:5173"]`.
With 4 other users hitting the server's LAN address instead of `localhost`,
this needs to allow that origin. Since there's no auth and no per-user
restriction (confirmed — shared department resource), the simplest correct
fix is allowing the server's actual LAN-reachable origin(s) via config
(env var), rather than hand-maintaining a per-browser allowlist.

## Download endpoint

New `GET /download/{image_id}`, same shape as the existing
`GET /thumbnail/{image_id}` (`main.py:84-89`) but returning
`FileResponse(entry.path, filename=Path(entry.path).name)` — the `filename`
argument makes FastAPI set `Content-Disposition: attachment`, so the browser
downloads the original NAS file instead of trying to display it inline.

## Rollout

Four pieces, one implementation pass:

1. `storage.py` rewritten onto SQLite (+ the `find_similar` vectorization fix
   in `search.py`), with migration built into `load()` so existing local dev
   state (the current 19-entry `index.json`) upgrades automatically — no
   manual migration step.
2. A `watchdog`-based watcher thread + infrequent reconciliation timer, wired
   up at app startup in `main.py`, reusing `Indexer.process_image()` and the
   new `IndexStore.delete_by_path()`.
3. CORS origin made configurable so the other 4 users can reach the server
   over the LAN.
4. A `GET /download/{image_id}` endpoint.

None of these change `ImageEntry`'s shape or `IndexStore`'s existing public
read methods, so `search.py`'s filtering logic and the frontend's existing
API calls are unaffected except where explicitly noted above.
