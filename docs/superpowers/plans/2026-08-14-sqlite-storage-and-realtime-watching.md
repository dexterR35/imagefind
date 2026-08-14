# SQLite Storage + Real-Time NAS Watching Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the JSON/npy index with SQLite (no more full-file rewrites at 300k+ images), vectorize similarity search, add a DAM-style real-time folder watcher with a low-frequency reconciliation backstop, and add the multi-user/LAN pieces (configurable CORS, a download endpoint).

**Architecture:** `IndexStore` (`backend/app/storage.py`) is rewritten internally onto a single SQLite file (`index.db`) while keeping its exact public interface, so `indexer.py`/`search.py`/`main.py` need almost no changes. A new `backend/app/watcher.py` module wraps the `watchdog` library to call the existing `Indexer.process_image()`/`IndexStore.upsert()`/new `IndexStore.delete_by_path()` per-file, with an infrequent reconciliation loop reusing the existing `run_reindex()` as a backstop. `main.py` gets a `download` endpoint and configurable CORS.

**Tech Stack:** Python 3.12, FastAPI, `sqlite3` (stdlib), `watchdog==6.0.0`, numpy, pytest.

**Spec:** `docs/superpowers/specs/2026-08-14-sqlite-index-store-design.md`

## Global Constraints

- `IndexStore`'s public interface (`all()`, `get()`, `get_by_path()`, `get_embedding()`, `upsert()`, `prune()`, `save()`, `load()`, `.entries`, `.embeddings`, `.lock`) must not change shape — `indexer.py`, `search.py`, `main.py` should require zero changes for the storage swap itself.
- Every write to `self.entries`/`self.embeddings`/`_by_id`/`_by_path` stays guarded by `self.lock` (existing `threading.RLock`), same as today — this is what already makes concurrent upserts safe.
- Embeddings are always L2-normalized `float32` — cosine similarity is a plain dot product (already true today, see `embeddings.py:38,47`).
- No new external services (no Postgres, no separate DB server) — SQLite file only, per the spec's approach decision.
- The watcher/reconciliation loop must default to **off** in tests — gated behind `config.ENABLE_WATCHER` (default `false`) — so the existing `_fresh_app()` test helper in `test_main.py`, which does `importlib.reload(main)` per test, never spins up real background threads/OS file watchers.

---

### Task 1: Rewrite `storage.py` onto SQLite (core CRUD)

**Files:**
- Modify: `backend/app/storage.py` (full rewrite)
- Modify: `backend/tests/test_storage.py:66-83` (remove the JSON/npy-specific mismatch test — replaced by Task 2's corrupt-db test)
- Test: `backend/tests/test_storage.py` (existing tests, unchanged except the removal above)

**Interfaces:**
- Produces: `IndexStore.__init__(index_dir: Path, embedding_dim: int = 512)`, `.load() -> None`, `.save() -> None`, `.upsert(entry: ImageEntry, embedding: np.ndarray) -> None`, `.get(id: str) -> ImageEntry | None`, `.get_by_path(path: str) -> ImageEntry | None`, `.get_embedding(id: str) -> np.ndarray | None`, `.all() -> list[ImageEntry]`, `.prune(keep_paths: set[str]) -> None`, `.needs_reindex(path: Path) -> bool` — identical signatures to today.
- Consumes: nothing new (same `ImageEntry` dataclass shape as today).

This task is a full-file replacement under existing test coverage, not new-feature TDD — `test_storage.py`'s tests already exist and already pass against the current JSON implementation. The discipline here is "keep the existing suite green through the rewrite," verified by running it before and after, not fabricating an artificial failing test for behavior that's already specified.

- [ ] **Step 1: Confirm the baseline — run the current suite before touching anything**

Run: `cd backend && .venv/bin/python3 -m pytest tests/test_storage.py -v`
Expected: all 8 tests PASS (current JSON-backed implementation).

- [ ] **Step 2: Remove the JSON/npy-specific corruption test**

In `backend/tests/test_storage.py`, delete this test (it simulates desync between two separate files, which can't happen once there's a single `index.db`; Task 2 replaces it with a SQLite-appropriate equivalent):

```python
def test_load_resets_to_empty_on_entries_embeddings_length_mismatch(tmp_path):
    store = IndexStore(tmp_path, embedding_dim=4)
    store.load()
    store.upsert(_entry(), np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32))
    store.save()

    # Simulate a desynced pair, e.g. from a crash between the two os.replace()
    # calls in save(), or embeddings.npy being deleted/replaced by hand.
    np.save(tmp_path / "embeddings.npy", np.zeros((0, 4), dtype=np.float32))

    reloaded = IndexStore(tmp_path, embedding_dim=4)
    reloaded.load()

    assert reloaded.all() == []
    assert reloaded.embeddings.shape == (0, 4)
    # Reading after the reset must not raise, unlike the desynced state would.
    assert reloaded.get("a1") is None
```

- [ ] **Step 3: Replace `backend/app/storage.py` entirely**

```python
import json
import logging
import sqlite3
import threading
from dataclasses import dataclass, fields
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS images (
    id TEXT PRIMARY KEY,
    path TEXT UNIQUE NOT NULL,
    thumbnail_path TEXT NOT NULL,
    ocr_text TEXT NOT NULL,
    colors TEXT NOT NULL,
    objects TEXT NOT NULL,
    mtime REAL NOT NULL,
    size INTEGER NOT NULL,
    embedding BLOB NOT NULL
)
"""


@dataclass
class ImageEntry:
    id: str
    path: str
    thumbnail_path: str
    ocr_text: str
    colors: list[str]
    objects: list[str]
    mtime: float
    size: int


class IndexStore:
    def __init__(self, index_dir: Path, embedding_dim: int = 512):
        self.index_dir = Path(index_dir)
        self.index_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.index_dir / "index.db"
        self.legacy_index_path = self.index_dir / "index.json"
        self.legacy_embeddings_path = self.index_dir / "embeddings.npy"
        self.embedding_dim = embedding_dim
        self.entries: list[ImageEntry] = []
        self.embeddings: np.ndarray = np.zeros((0, embedding_dim), dtype=np.float32)
        self._by_id: dict[str, int] = {}
        self._by_path: dict[str, int] = {}
        self.lock = threading.RLock()
        self._conn = self._open_db()

    def _open_db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            conn.execute(_SCHEMA)
            conn.commit()
        except sqlite3.DatabaseError:
            logger.warning(
                "IndexStore: %s is corrupt, resetting to a fresh empty index", self.db_path
            )
            conn.close()
            self.db_path.unlink(missing_ok=True)
            conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(_SCHEMA)
            conn.commit()
        return conn

    def load(self) -> None:
        with self.lock:
            rows = self._conn.execute(
                "SELECT id, path, thumbnail_path, ocr_text, colors, objects, "
                "mtime, size, embedding FROM images ORDER BY rowid"
            ).fetchall()

            self.entries = []
            embeddings = []
            for (id_, path, thumbnail_path, ocr_text, colors_json, objects_json,
                 mtime, size, embedding_blob) in rows:
                self.entries.append(ImageEntry(
                    id=id_, path=path, thumbnail_path=thumbnail_path,
                    ocr_text=ocr_text, colors=json.loads(colors_json),
                    objects=json.loads(objects_json), mtime=mtime, size=size,
                ))
                embeddings.append(np.frombuffer(embedding_blob, dtype=np.float32))

            self.embeddings = (
                np.vstack(embeddings) if embeddings
                else np.zeros((0, self.embedding_dim), dtype=np.float32)
            )
            self._reindex_lookup()

    def _reindex_lookup(self) -> None:
        self._by_id = {e.id: i for i, e in enumerate(self.entries)}
        self._by_path = {e.path: i for i, e in enumerate(self.entries)}

    def save(self) -> None:
        with self.lock:
            self._conn.commit()

    def needs_reindex(self, path: Path) -> bool:
        with self.lock:
            key = str(path)
            if key not in self._by_path:
                return True
            entry = self.entries[self._by_path[key]]
        stat = Path(path).stat()
        return entry.mtime != stat.st_mtime or entry.size != stat.st_size

    def upsert(self, entry: ImageEntry, embedding: np.ndarray) -> None:
        with self.lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO images "
                "(id, path, thumbnail_path, ocr_text, colors, objects, mtime, size, embedding) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    entry.id, entry.path, entry.thumbnail_path, entry.ocr_text,
                    json.dumps(entry.colors), json.dumps(entry.objects),
                    entry.mtime, entry.size,
                    np.asarray(embedding, dtype=np.float32).tobytes(),
                ),
            )
            if entry.path in self._by_path:
                i = self._by_path[entry.path]
                old_id = self.entries[i].id
                self.entries[i] = entry
                self.embeddings[i] = embedding
                if old_id != entry.id:
                    del self._by_id[old_id]
            else:
                i = len(self.entries)
                self.entries.append(entry)
                self.embeddings = np.vstack([self.embeddings, embedding[None, :]])
                self._by_path[entry.path] = i
            self._by_id[entry.id] = i

    def prune(self, keep_paths: set[str]) -> None:
        with self.lock:
            self._conn.execute("CREATE TEMP TABLE IF NOT EXISTS keep_paths (path TEXT PRIMARY KEY)")
            self._conn.execute("DELETE FROM keep_paths")
            self._conn.executemany(
                "INSERT INTO keep_paths (path) VALUES (?)", [(p,) for p in keep_paths]
            )
            self._conn.execute("DELETE FROM images WHERE path NOT IN (SELECT path FROM keep_paths)")
            self._conn.execute("DROP TABLE keep_paths")
            self._conn.commit()

            keep_indices = [i for i, e in enumerate(self.entries) if e.path in keep_paths]
            self.entries = [self.entries[i] for i in keep_indices]
            if keep_indices:
                self.embeddings = self.embeddings[keep_indices]
            else:
                self.embeddings = np.zeros((0, self.embedding_dim), dtype=np.float32)
            self._reindex_lookup()

    def get(self, id: str) -> ImageEntry | None:
        with self.lock:
            i = self._by_id.get(id)
            return self.entries[i] if i is not None else None

    def get_by_path(self, path: str) -> ImageEntry | None:
        with self.lock:
            i = self._by_path.get(path)
            return self.entries[i] if i is not None else None

    def get_embedding(self, id: str) -> np.ndarray | None:
        with self.lock:
            i = self._by_id.get(id)
            return self.embeddings[i] if i is not None else None

    def all(self) -> list[ImageEntry]:
        with self.lock:
            return list(self.entries)
```

Note: `fields` is imported but unused by this task alone — it's used starting Task 3 (migration). Leave the import; Task 3 needs it.

- [ ] **Step 4: Run the suite, confirm it passes**

Run: `cd backend && .venv/bin/python3 -m pytest tests/test_storage.py -v`
Expected: all 7 remaining tests PASS (the 8th was removed in Step 2).

- [ ] **Step 5: Run the full backend suite to check for regressions elsewhere**

Run: `cd backend && .venv/bin/python3 -m pytest tests/ -q`
Expected: same pass/fail counts as the pre-existing baseline (`test_objects.py::test_clear_custom_tag_cache_forces_recomputation` was already failing before this change, unrelated — confirm no *new* failures).

- [ ] **Step 6: Commit**

```bash
git add backend/app/storage.py backend/tests/test_storage.py
git commit -m "refactor: rewrite IndexStore onto SQLite instead of JSON+npy"
```

---

### Task 2: Corrupt-database recovery

**Files:**
- Test: `backend/tests/test_storage.py`

**Interfaces:**
- Consumes: `IndexStore.__init__`, `.load()`, `.all()`, `.get()` from Task 1.
- Produces: nothing new — this task verifies `_open_db()`'s existing try/except (written in Task 1) actually does its job; no new production code beyond confirming it.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_storage.py`:

```python
def test_corrupt_db_resets_to_empty(tmp_path):
    (tmp_path / "index.db").write_bytes(b"not a real sqlite database")

    store = IndexStore(tmp_path, embedding_dim=4)
    store.load()

    assert store.all() == []
    assert store.embeddings.shape == (0, 4)
    assert store.get("a1") is None
```

- [ ] **Step 2: Run it to verify it fails for the expected reason**

Run: `cd backend && .venv/bin/python3 -m pytest tests/test_storage.py::test_corrupt_db_resets_to_empty -v`

At this point Task 1's `_open_db()` already contains the try/except — if Step 1's test passes immediately, that's fine (it means Task 1's implementation already handles it correctly); if it fails, inspect the traceback: it should be a `sqlite3.DatabaseError` propagating out of `_open_db()`, not a different error. If it's a different error, fix `_open_db()`'s except clause to catch `sqlite3.DatabaseError` specifically before proceeding.

- [ ] **Step 3: Confirm passing (implementation already exists from Task 1; this step is verification, not new code)**

Run: `cd backend && .venv/bin/python3 -m pytest tests/test_storage.py -v`
Expected: all tests PASS, including the new one.

- [ ] **Step 4: Commit**

```bash
git add backend/tests/test_storage.py
git commit -m "test: verify IndexStore resets to empty on a corrupt index.db"
```

---

### Task 3: Migrate legacy `index.json`/`embeddings.npy` on first load

**Files:**
- Modify: `backend/app/storage.py`
- Test: `backend/tests/test_storage.py`

**Interfaces:**
- Consumes: `fields(ImageEntry)` (already imported in Task 1).
- Produces: `IndexStore._migrate_from_legacy_files() -> None` (private, called from `load()`).

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_storage.py`:

```python
import json as _json  # only if not already imported at module level; otherwise use existing import


def test_migrates_legacy_json_and_npy_on_first_load(tmp_path):
    legacy_entry = {
        "id": "a1", "path": "/imgs/a.png", "thumbnail_path": "/thumbs/a1.jpg",
        "ocr_text": "NETBET", "colors": ["green"], "objects": ["clover"],
        "mtime": 123.0, "size": 456,
    }
    (tmp_path / "index.json").write_text(_json.dumps([legacy_entry]))
    np.save(tmp_path / "embeddings.npy", np.array([[1.0, 0.0, 0.0, 0.0]], dtype=np.float32))

    store = IndexStore(tmp_path, embedding_dim=4)
    store.load()

    assert store.get("a1").ocr_text == "NETBET"
    assert store.get_embedding("a1").tolist() == [1.0, 0.0, 0.0, 0.0]
    assert store.get_by_path("/imgs/a.png").id == "a1"
```

(If `test_storage.py` doesn't already import `json` at the top, add `import json` at the top of the file instead of the inline alias above and use `json.dumps` directly — check the file's existing imports first.)

- [ ] **Step 2: Run it to verify it fails**

Run: `cd backend && .venv/bin/python3 -m pytest tests/test_storage.py::test_migrates_legacy_json_and_npy_on_first_load -v`
Expected: FAIL — `store.get("a1")` returns `None` (nothing migrated yet, `load()` doesn't look at legacy files).

- [ ] **Step 3: Implement the migration**

In `backend/app/storage.py`, add this method to `IndexStore` and call it from `load()`:

```python
    def _migrate_from_legacy_files(self) -> None:
        try:
            data = json.loads(self.legacy_index_path.read_text())
        except (OSError, json.JSONDecodeError):
            logger.warning("IndexStore: legacy index.json unreadable, skipping migration")
            return
        try:
            legacy_embeddings = np.load(self.legacy_embeddings_path)
        except OSError:
            logger.warning("IndexStore: legacy embeddings.npy unreadable, skipping migration")
            return
        if len(data) != legacy_embeddings.shape[0]:
            logger.warning(
                "IndexStore: legacy entries/embeddings length mismatch, skipping migration"
            )
            return

        known_fields = {f.name for f in fields(ImageEntry)}
        rows = []
        for i, e in enumerate(data):
            entry = ImageEntry(**{k: v for k, v in e.items() if k in known_fields})
            rows.append((
                entry.id, entry.path, entry.thumbnail_path, entry.ocr_text,
                json.dumps(entry.colors), json.dumps(entry.objects),
                entry.mtime, entry.size,
                legacy_embeddings[i].astype(np.float32).tobytes(),
            ))
        self._conn.executemany(
            "INSERT OR REPLACE INTO images "
            "(id, path, thumbnail_path, ocr_text, colors, objects, mtime, size, embedding) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        self._conn.commit()
```

Update `load()`'s first line to call it when appropriate — insert this at the top of `load()`, before the `SELECT`:

```python
    def load(self) -> None:
        with self.lock:
            if self.legacy_index_path.exists():
                count = self._conn.execute("SELECT COUNT(*) FROM images").fetchone()[0]
                if count == 0:
                    self._migrate_from_legacy_files()

            rows = self._conn.execute(
                ...
```

(Keep the rest of `load()` exactly as Task 1 wrote it.)

- [ ] **Step 4: Run the test, confirm it passes**

Run: `cd backend && .venv/bin/python3 -m pytest tests/test_storage.py -v`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/storage.py backend/tests/test_storage.py
git commit -m "feat: migrate legacy index.json/embeddings.npy into index.db on first load"
```

---

### Task 4: `delete_by_path` (needed by the watcher)

**Files:**
- Modify: `backend/app/storage.py`
- Test: `backend/tests/test_storage.py`

**Interfaces:**
- Produces: `IndexStore.delete_by_path(path: str) -> None`.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_storage.py`:

```python
def test_delete_by_path_removes_entry_and_keeps_embeddings_aligned(tmp_path):
    store = IndexStore(tmp_path, embedding_dim=4)
    store.load()
    store.upsert(_entry(id="a1", path="/imgs/a.png"), np.array([1, 0, 0, 0], dtype=np.float32))
    store.upsert(_entry(id="b1", path="/imgs/b.png"), np.array([0, 1, 0, 0], dtype=np.float32))

    store.delete_by_path("/imgs/a.png")

    assert store.get("a1") is None
    assert [e.id for e in store.all()] == ["b1"]
    assert store.embeddings.shape[0] == 1
    assert store.get_embedding("b1").tolist() == [0, 1, 0, 0]

    reloaded = IndexStore(tmp_path, embedding_dim=4)
    reloaded.load()
    assert reloaded.get("a1") is None
```

- [ ] **Step 2: Run it, confirm it fails**

Run: `cd backend && .venv/bin/python3 -m pytest tests/test_storage.py::test_delete_by_path_removes_entry_and_keeps_embeddings_aligned -v`
Expected: FAIL — `AttributeError: 'IndexStore' object has no attribute 'delete_by_path'`.

- [ ] **Step 3: Implement it**

Add to `backend/app/storage.py`:

```python
    def delete_by_path(self, path: str) -> None:
        with self.lock:
            self._conn.execute("DELETE FROM images WHERE path = ?", (path,))
            self._conn.commit()
            i = self._by_path.get(path)
            if i is None:
                return
            del self.entries[i]
            self.embeddings = np.delete(self.embeddings, i, axis=0)
            self._reindex_lookup()
```

- [ ] **Step 4: Run it, confirm it passes**

Run: `cd backend && .venv/bin/python3 -m pytest tests/test_storage.py -v`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/storage.py backend/tests/test_storage.py
git commit -m "feat: add IndexStore.delete_by_path for single-file deletes"
```

---

### Task 5: Vectorize `find_similar()` in `search.py`

**Files:**
- Modify: `backend/app/search.py:38-59`
- Test: `backend/tests/test_search.py`

**Interfaces:**
- Consumes: `store.embeddings` (numpy array), `store._by_id` (existing private dict), `store.all()`, `store.get()` — same as today.
- Produces: `find_similar(store, image_id, limit=20) -> list[ImageEntry] | None` — identical signature and contract (excludes self, orders by similarity descending, returns `None` for unknown id, respects `limit`).

This is a refactor under existing coverage (`test_find_similar_excludes_self_and_orders_by_similarity`, `test_find_similar_unknown_id_returns_none` already pass against the current loop-based implementation) — plus one genuinely new test for the `limit`+top-k behavior at a scale the existing 3-entry tests don't exercise.

- [ ] **Step 1: Confirm baseline**

Run: `cd backend && .venv/bin/python3 -m pytest tests/test_search.py -v`
Expected: all 6 tests PASS.

- [ ] **Step 2: Write a new failing test for large-scale + limit behavior**

Add to `backend/tests/test_search.py`:

```python
def test_find_similar_respects_limit_with_many_entries(tmp_path):
    entries_and_vecs = [(_entry("query"), [1.0, 0.0])]
    # 50 entries with descending similarity to the query vector [1,0],
    # so the correct top-5 (excluding the query itself) is deterministic.
    for i in range(50):
        angle_component = i / 100.0
        entries_and_vecs.append(
            (_entry(f"e{i}"), [1.0 - angle_component, angle_component])
        )
    store = _store_with(tmp_path, entries_and_vecs)

    result = find_similar(store, "query", limit=5)

    assert len(result) == 5
    assert [e.id for e in result] == ["e0", "e1", "e2", "e3", "e4"]
```

- [ ] **Step 3: Run it, confirm it fails for the expected reason**

Run: `cd backend && .venv/bin/python3 -m pytest tests/test_search.py::test_find_similar_respects_limit_with_many_entries -v`

Expected: PASS is also acceptable here (the old loop implementation is correct, just slow) — if it already passes, that's fine; the point of this step is confirming the test is well-formed and asserts something meaningful, not that it must fail. If it fails, read the assertion error and fix the test's expected ordering before proceeding (don't change the assertion to match a bug).

- [ ] **Step 4: Replace `find_similar()`**

In `backend/app/search.py`, replace:

```python
def find_similar(store: IndexStore, image_id: str, limit: int = 20) -> list[ImageEntry] | None:
    """Returns None if image_id doesn't exist, distinct from an empty list
    (which means it exists but has no similar images) — the caller (the
    /search/similar/{id} endpoint) needs that distinction to return 404 vs
    200, and doing the existence check and the lookup under the same lock
    acquisition (rather than as two separate store.get() calls at different
    times) avoids a race where a concurrent prune removes the entry between
    them."""
    with store.lock:
        entry = store.get(image_id)
        if entry is None:
            return None
        query_embedding = store.get_embedding(image_id)
        entries = store.all()
        scored = []
        for i, other in enumerate(entries):
            if other.id == image_id:
                continue
            sim = embeddings.cosine_similarity(query_embedding, store.embeddings[i])
            scored.append((sim, i))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [entries[i] for _, i in scored[:limit]]
```

with:

```python
def find_similar(store: IndexStore, image_id: str, limit: int = 20) -> list[ImageEntry] | None:
    """Returns None if image_id doesn't exist, distinct from an empty list
    (which means it exists but has no similar images) — the caller (the
    /search/similar/{id} endpoint) needs that distinction to return 404 vs
    200, and doing the existence check and the lookup under the same lock
    acquisition (rather than as two separate store.get() calls at different
    times) avoids a race where a concurrent prune removes the entry between
    them.

    Similarity is one vectorized matrix-vector product against every stored
    embedding, not a per-entry Python loop — at hundreds of thousands of
    entries the loop was the actual bottleneck, not model inference."""
    with store.lock:
        entry = store.get(image_id)
        if entry is None:
            return None
        self_index = store._by_id[image_id]
        query_embedding = store.get_embedding(image_id)
        entries = store.all()

        if len(entries) <= 1:
            return []

        sims = store.embeddings @ query_embedding
        sims[self_index] = -np.inf

        k = min(limit, len(entries) - 1)
        top = np.argpartition(-sims, k - 1)[:k]
        top = top[np.argsort(-sims[top])]
        return [entries[i] for i in top]
```

Add `import numpy as np` to the top of `backend/app/search.py` if it isn't already imported (check the current imports first — the file currently only imports `from . import embeddings` and `from .storage import ImageEntry, IndexStore`, so `numpy` needs adding, and the `embeddings.cosine_similarity` import becomes unused for `find_similar` — check whether `search()` elsewhere in the file still uses it before removing the `embeddings` import; it doesn't, per the current file, so once this change lands `from . import embeddings` becomes fully unused and should be deleted).

- [ ] **Step 5: Run the tests, confirm all pass**

Run: `cd backend && .venv/bin/python3 -m pytest tests/test_search.py -v`
Expected: all 7 tests PASS (6 existing + 1 new).

- [ ] **Step 6: Commit**

```bash
git add backend/app/search.py backend/tests/test_search.py
git commit -m "perf: vectorize find_similar instead of a per-entry Python loop"
```

---

### Task 6: File watcher — create/modify handling with debounce

**Files:**
- Create: `backend/app/watcher.py`
- Create: `backend/tests/test_watcher.py`
- Modify: `backend/requirements.txt` (add `watchdog==6.0.0`)
- Modify: `backend/app/config.py` (add `ENABLE_WATCHER`, `WATCHER_STABLE_CHECK_SECONDS`)

**Interfaces:**
- Consumes: `Indexer.process_image(path: Path, settings: ReindexSettings) -> tuple[ImageEntry, np.ndarray]` and `Indexer._current_settings() -> ReindexSettings` (both already exist in `indexer.py`), `IndexStore.upsert()`/`.save()` (Task 1), `Indexer.images_dir` attribute (already exists).
- Produces: `start_watcher(indexer: Indexer, store: IndexStore) -> watchdog.observers.Observer`, plus the internal `_Handler` class and `_wait_until_stable(path, checks=3) -> bool` (used again in Task 7/8, keep them module-level so tests can call them directly).

- [ ] **Step 1: Add the dependency**

In `backend/requirements.txt`, add this line after `easyocr==1.7.2`'s block (near the other direct dependencies):

```
watchdog==6.0.0
```

Run: `cd backend && .venv/bin/pip install -r requirements.txt` (or `.venv/bin/python3 -m pip install watchdog==6.0.0` directly if the full requirements install is slow) to make it available locally before writing tests against it.

- [ ] **Step 2: Add config values**

In `backend/app/config.py`, add near the other `RAM_*`/color settings (after `COLOR_MIN_SHARE`):

```python
# DAM-style real-time indexing: a watchdog observer processes new/changed
# files as they land instead of waiting for a manual reindex. Off by default
# so tests (which reload config/main repeatedly) never spin up real
# background threads/OS file watchers; set true in the actual deployment.
ENABLE_WATCHER = os.environ.get("ENABLE_WATCHER", "false").lower() == "true"
# How long a file's size must stay unchanged across polls before the watcher
# treats a create/modify event as "the write is finished" and processes it —
# guards against reading a file mid-copy over the NAS.
WATCHER_STABLE_CHECK_SECONDS = float(os.environ.get("WATCHER_STABLE_CHECK_SECONDS", "1.0"))
```

- [ ] **Step 3: Write the failing tests**

Create `backend/tests/test_watcher.py`:

```python
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image

from app.indexer import Indexer
from app.storage import IndexStore
from app.watcher import _Handler, _wait_until_stable


def _fake_event(path, is_directory=False):
    return SimpleNamespace(src_path=str(path), is_directory=is_directory)


def test_wait_until_stable_returns_true_once_size_stops_changing(tmp_path):
    path = tmp_path / "photo.png"
    path.write_bytes(b"x" * 100)
    assert _wait_until_stable(path, checks=2, interval=0.01) is True


def test_wait_until_stable_returns_false_for_missing_file(tmp_path):
    assert _wait_until_stable(tmp_path / "missing.png", checks=2, interval=0.01) is False


def test_wait_until_stable_returns_false_when_size_keeps_changing(tmp_path, monkeypatch):
    path = tmp_path / "still-copying.png"
    path.write_bytes(b"x")
    sizes = iter([10, 20, 30, 40])
    # Instance-level monkeypatch (not the whole Path class) so this only
    # affects this one path object, not every stat() call in the test run.
    monkeypatch.setattr(path, "stat", lambda: SimpleNamespace(st_size=next(sizes)))
    assert _wait_until_stable(path, checks=3, interval=0.01) is False


def test_handler_on_modified_processes_and_upserts_image(tmp_path, monkeypatch):
    images_dir = tmp_path / "images"
    images_dir.mkdir()
    index_dir = tmp_path / "index"
    img_path = images_dir / "new.png"
    Image.new("RGB", (32, 32), (10, 20, 30)).save(img_path)

    store = IndexStore(index_dir, embedding_dim=512)
    store.load()
    indexer = Indexer(images_dir, index_dir, store)

    def fake_process_image(path, settings):
        stat = path.stat()
        from app.storage import ImageEntry
        entry = ImageEntry(
            id="new1", path=str(path), thumbnail_path=str(index_dir / "t.jpg"),
            ocr_text="", colors=[], objects=[], mtime=stat.st_mtime, size=stat.st_size,
        )
        return entry, np.zeros(512, dtype=np.float32)

    monkeypatch.setattr(indexer, "process_image", fake_process_image)
    monkeypatch.setattr("app.watcher._STABLE_CHECK_INTERVAL", 0.01)

    handler = _Handler(indexer, store)
    handler.on_modified(_fake_event(img_path))

    assert store.get_by_path(str(img_path)) is not None
    assert store.get_by_path(str(img_path)).id == "new1"


def test_handler_ignores_non_image_and_directory_events(tmp_path, monkeypatch):
    images_dir = tmp_path / "images"
    images_dir.mkdir()
    index_dir = tmp_path / "index"
    store = IndexStore(index_dir, embedding_dim=512)
    store.load()
    indexer = Indexer(images_dir, index_dir, store)

    calls = []
    monkeypatch.setattr(indexer, "process_image", lambda path, settings: calls.append(path))

    handler = _Handler(indexer, store)
    handler.on_modified(_fake_event(images_dir / "notes.txt"))
    handler.on_modified(_fake_event(images_dir / "subfolder", is_directory=True))

    assert calls == []
```

- [ ] **Step 4: Run the tests, confirm they fail**

Run: `cd backend && .venv/bin/python3 -m pytest tests/test_watcher.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.watcher'`.

- [ ] **Step 5: Implement `backend/app/watcher.py`**

```python
import logging
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from .indexer import IMAGE_EXTENSIONS, Indexer
from .storage import IndexStore

logger = logging.getLogger(__name__)

_STABLE_CHECK_INTERVAL = 1.0


def _wait_until_stable(path: Path, checks: int = 3, interval: float = _STABLE_CHECK_INTERVAL) -> bool:
    """False means "still changing" (or missing) after `checks` attempts —
    the caller should skip this event rather than read a partial file. A
    later event (or the reconciliation backstop) will catch it once the
    write actually finishes."""
    last_size = -1
    for _ in range(checks):
        try:
            size = path.stat().st_size
        except OSError:
            return False
        if size == last_size:
            return True
        last_size = size
        time.sleep(interval)
    return False


class _Handler(FileSystemEventHandler):
    def __init__(self, indexer: Indexer, store: IndexStore):
        self._indexer = indexer
        self._store = store

    def on_created(self, event):
        self._handle_changed(event)

    def on_modified(self, event):
        self._handle_changed(event)

    def on_deleted(self, event):
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.suffix.lower() not in IMAGE_EXTENSIONS:
            return
        self._store.delete_by_path(str(path))
        self._store.save()

    def _handle_changed(self, event):
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.suffix.lower() not in IMAGE_EXTENSIONS:
            return
        if not _wait_until_stable(path, interval=_STABLE_CHECK_INTERVAL):
            return
        settings = self._indexer._current_settings()
        try:
            entry, embedding = self._indexer.process_image(path, settings)
        except Exception:
            logger.warning("watcher: failed to process %s", path, exc_info=True)
            return
        self._store.upsert(entry, embedding)
        self._store.save()


def start_watcher(indexer: Indexer, store: IndexStore) -> Observer:
    observer = Observer()
    observer.schedule(_Handler(indexer, store), str(indexer.images_dir), recursive=True)
    observer.start()
    return observer
```

Note the test in Step 3 does `monkeypatch.setattr("app.watcher._STABLE_CHECK_INTERVAL", 0.01)` — this only speeds up the interval used *inside* `_handle_changed`'s call to `_wait_until_stable`, since that call reads the module-level `_STABLE_CHECK_INTERVAL` at call time. The `_wait_until_stable` tests in Step 3 instead pass `interval=0.01` directly as an argument, which is why the function takes `interval` as an explicit parameter rather than only reading the module global.

- [ ] **Step 6: Run the tests, confirm they pass**

Run: `cd backend && .venv/bin/python3 -m pytest tests/test_watcher.py -v`
Expected: all 4 tests PASS.

- [ ] **Step 7: Run the full suite for regressions**

Run: `cd backend && .venv/bin/python3 -m pytest tests/ -q`
Expected: same baseline as before (only the pre-existing unrelated `test_objects.py` failure).

- [ ] **Step 8: Commit**

```bash
git add backend/app/watcher.py backend/tests/test_watcher.py backend/requirements.txt backend/app/config.py
git commit -m "feat: add watchdog-based file watcher for real-time NAS indexing"
```

---

### Task 7: File watcher — delete handling test coverage

**Files:**
- Test: `backend/tests/test_watcher.py`

**Interfaces:**
- Consumes: `_Handler.on_deleted` (already implemented in Task 6, Step 5) — this task adds the test that was deferred to keep Task 6 from growing too large.

- [ ] **Step 1: Write the failing-if-wrong test**

Add to `backend/tests/test_watcher.py`:

```python
def test_handler_on_deleted_removes_entry(tmp_path):
    images_dir = tmp_path / "images"
    images_dir.mkdir()
    index_dir = tmp_path / "index"
    img_path = images_dir / "gone.png"

    store = IndexStore(index_dir, embedding_dim=512)
    store.load()
    from app.storage import ImageEntry
    store.upsert(
        ImageEntry(
            id="gone1", path=str(img_path), thumbnail_path=str(index_dir / "t.jpg"),
            ocr_text="", colors=[], objects=[], mtime=0.0, size=0,
        ),
        np.zeros(512, dtype=np.float32),
    )
    indexer = Indexer(images_dir, index_dir, store)

    handler = _Handler(indexer, store)
    handler.on_deleted(_fake_event(img_path))

    assert store.get_by_path(str(img_path)) is None
```

- [ ] **Step 2: Run it**

Run: `cd backend && .venv/bin/python3 -m pytest tests/test_watcher.py::test_handler_on_deleted_removes_entry -v`
Expected: PASS (the implementation already exists from Task 6 — this step exists to give `on_deleted` its own explicit regression test, since Task 6 only implemented it without a dedicated test).

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_watcher.py
git commit -m "test: cover watcher delete-event handling"
```

---

### Task 8: Reconciliation loop + wiring into `main.py`

**Files:**
- Modify: `backend/app/watcher.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/config.py`
- Test: `backend/tests/test_watcher.py`

**Interfaces:**
- Produces: `start_reconciliation_loop(indexer: Indexer, job_factory: Callable[[], ReindexJob], interval_seconds: float, stop_event: threading.Event) -> threading.Thread`.
- Consumes: `Indexer.run_reindex(job: ReindexJob, force: bool = False) -> None` (already exists).

- [ ] **Step 1: Add the reconciliation interval config**

In `backend/app/config.py`, add next to `WATCHER_STABLE_CHECK_SECONDS`:

```python
# Backstop for the real-time watcher: catches anything a missed file-system
# event didn't (e.g. SMB change-notifications aren't 100% guaranteed for
# writes from other machines onto the same NAS share). This is a full
# needs_reindex()-gated scan, same as the manual Reindex button, just run on
# a low-frequency timer instead of only on click.
RECONCILE_INTERVAL_SECONDS = float(os.environ.get("RECONCILE_INTERVAL_SECONDS", str(4 * 60 * 60)))
```

- [ ] **Step 2: Write the failing test**

Add to `backend/tests/test_watcher.py`:

```python
import threading

from app.indexer import ReindexJob
from app.watcher import start_reconciliation_loop


def test_reconciliation_loop_calls_run_reindex_on_interval(tmp_path, monkeypatch):
    images_dir = tmp_path / "images"
    images_dir.mkdir()
    index_dir = tmp_path / "index"
    store = IndexStore(index_dir, embedding_dim=512)
    store.load()
    indexer = Indexer(images_dir, index_dir, store)

    calls = []
    monkeypatch.setattr(indexer, "run_reindex", lambda job, force=False: calls.append(job))

    stop_event = threading.Event()
    thread = start_reconciliation_loop(
        indexer, lambda: ReindexJob(id="r1"), interval_seconds=0.01, stop_event=stop_event
    )
    time.sleep(0.05)
    stop_event.set()
    thread.join(timeout=2)

    assert len(calls) >= 1
    assert not thread.is_alive()
```

- [ ] **Step 3: Run it, confirm it fails**

Run: `cd backend && .venv/bin/python3 -m pytest tests/test_watcher.py::test_reconciliation_loop_calls_run_reindex_on_interval -v`
Expected: FAIL — `ImportError: cannot import name 'start_reconciliation_loop'`.

- [ ] **Step 4: Implement it**

Add to `backend/app/watcher.py` (add `import threading` and `from typing import Callable` to the top, alongside the existing imports; add the new function at the bottom):

```python
def start_reconciliation_loop(
    indexer: Indexer,
    job_factory: Callable[[], "ReindexJob"],
    interval_seconds: float,
    stop_event: threading.Event,
) -> threading.Thread:
    def loop():
        while not stop_event.wait(interval_seconds):
            job = job_factory()
            try:
                indexer.run_reindex(job)
            except Exception:
                logger.warning("reconciliation: run_reindex failed", exc_info=True)

    thread = threading.Thread(target=loop, daemon=True)
    thread.start()
    return thread
```

`ReindexJob` only needs to be a type-hint reference here (the caller supplies actual instances via `job_factory`), so import it under `TYPE_CHECKING` to avoid a circular import concern — add near the top:

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .indexer import ReindexJob
```

- [ ] **Step 5: Run the test, confirm it passes**

Run: `cd backend && .venv/bin/python3 -m pytest tests/test_watcher.py -v`
Expected: all tests PASS.

- [ ] **Step 6: Wire both into `main.py`**

In `backend/app/main.py`, after the existing `indexer = Indexer(...)` line (`main.py:26`), add:

```python
_watcher_observer = None
_reconciliation_stop = threading.Event()
_reconciliation_thread = None

if config.ENABLE_WATCHER:
    from .watcher import start_reconciliation_loop, start_watcher

    _watcher_observer = start_watcher(indexer, store)
    _reconciliation_thread = start_reconciliation_loop(
        indexer,
        lambda: ReindexJob(id=uuid.uuid4().hex),
        config.RECONCILE_INTERVAL_SECONDS,
        _reconciliation_stop,
    )


@app.on_event("shutdown")
def _stop_watcher():
    if _watcher_observer is not None:
        _watcher_observer.stop()
        _watcher_observer.join(timeout=5)
    _reconciliation_stop.set()
    if _reconciliation_thread is not None:
        _reconciliation_thread.join(timeout=5)
```

`threading` is already imported at the top of `main.py` (`main.py:1`); `ReindexJob` is already imported (`main.py:11`); `uuid` is already imported (`main.py:2`) — no new imports needed beyond the conditional `from .watcher import ...` inside the `if` block (kept local to avoid importing `watchdog` at all when the watcher is disabled).

- [ ] **Step 7: Add a smoke test confirming it stays off by default**

Add to `backend/tests/test_main.py` (reuse the existing `_fresh_app` helper pattern already in the file):

```python
def test_watcher_disabled_by_default(tmp_path, monkeypatch):
    main, _ = _fresh_app(tmp_path, monkeypatch)
    assert main._watcher_observer is None
    assert main._reconciliation_thread is None
```

- [ ] **Step 8: Run the full main test suite**

Run: `cd backend && .venv/bin/python3 -m pytest tests/test_main.py tests/test_watcher.py -v`
Expected: all PASS.

- [ ] **Step 9: Commit**

```bash
git add backend/app/watcher.py backend/app/main.py backend/app/config.py backend/tests/test_watcher.py backend/tests/test_main.py
git commit -m "feat: add reconciliation loop and wire watcher into app startup/shutdown"
```

---

### Task 9: Configurable CORS for LAN access

**Files:**
- Modify: `backend/app/config.py`
- Modify: `backend/app/main.py:17-22`
- Test: `backend/tests/test_main.py`

**Interfaces:**
- Produces: `config.CORS_ALLOWED_ORIGINS: list[str]`.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_main.py`:

```python
def test_cors_allowed_origins_configurable_via_env(tmp_path, monkeypatch):
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "http://192.168.1.50:5173,http://localhost:5173")
    main, _ = _fresh_app(tmp_path, monkeypatch)
    assert main.config.CORS_ALLOWED_ORIGINS == [
        "http://192.168.1.50:5173", "http://localhost:5173"
    ]
```

- [ ] **Step 2: Run it, confirm it fails**

Run: `cd backend && .venv/bin/python3 -m pytest tests/test_main.py::test_cors_allowed_origins_configurable_via_env -v`
Expected: FAIL — `AttributeError: module 'app.config' has no attribute 'CORS_ALLOWED_ORIGINS'`.

- [ ] **Step 3: Add the config value**

In `backend/app/config.py`, add near the top (after `IMAGES_DIR`/before the RAM section, or anywhere at module level):

```python
# Which browser origins may call this API. Defaults to the Vite dev server
# only; set to the server machine's actual LAN address(es) — comma-separated
# — once other users on the network need to reach it (e.g.
# "http://192.168.1.50:5173,http://192.168.1.50:3000").
CORS_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("CORS_ALLOWED_ORIGINS", "http://localhost:5173").split(",")
    if origin.strip()
]
```

- [ ] **Step 4: Use it in `main.py`**

Replace in `backend/app/main.py`:

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)
```

with:

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

- [ ] **Step 5: Run the test, confirm it passes**

Run: `cd backend && .venv/bin/python3 -m pytest tests/test_main.py -v`
Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/config.py backend/app/main.py backend/tests/test_main.py
git commit -m "feat: make CORS allowed origins configurable for LAN access"
```

---

### Task 10: Download endpoint

**Files:**
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_main.py`

**Interfaces:**
- Produces: `GET /download/{image_id}` — 404 if unknown, otherwise `FileResponse` with `Content-Disposition: attachment`.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_main.py`:

```python
def test_download_endpoint_returns_original_file_as_attachment(tmp_path, monkeypatch):
    main, _ = _fresh_app(tmp_path, monkeypatch)
    from app.storage import ImageEntry

    original = tmp_path / "original.png"
    original.write_bytes(b"fake-png-bytes")
    entry = ImageEntry(
        id="d1", path=str(original), thumbnail_path=str(tmp_path / "d1.jpg"),
        ocr_text="", colors=[], objects=[], mtime=0.0, size=0,
    )
    main.store.upsert(entry, np.ones(512, dtype=np.float32))

    client = TestClient(main.app)
    response = client.get("/download/d1")

    assert response.status_code == 200
    assert response.content == b"fake-png-bytes"
    assert "attachment" in response.headers["content-disposition"]
    assert "original.png" in response.headers["content-disposition"]


def test_download_endpoint_404_for_unknown_id(tmp_path, monkeypatch):
    main, _ = _fresh_app(tmp_path, monkeypatch)
    client = TestClient(main.app)
    assert client.get("/download/missing").status_code == 404
```

- [ ] **Step 2: Run it, confirm it fails**

Run: `cd backend && .venv/bin/python3 -m pytest tests/test_main.py::test_download_endpoint_returns_original_file_as_attachment -v`
Expected: FAIL — `404` (route doesn't exist).

- [ ] **Step 3: Implement it**

In `backend/app/main.py`, add near the existing `/thumbnail/{image_id}` endpoint (`main.py:84-89`):

```python
@app.get("/download/{image_id}")
def download_endpoint(image_id: str):
    entry = store.get(image_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="image not found")
    return FileResponse(entry.path, filename=Path(entry.path).name)
```

`FileResponse` and `Path` are already imported at the top of `main.py` (`main.py:3,7`) — no new imports needed.

- [ ] **Step 4: Run the tests, confirm they pass**

Run: `cd backend && .venv/bin/python3 -m pytest tests/test_main.py -v`
Expected: all tests PASS.

- [ ] **Step 5: Run the entire backend suite one final time**

Run: `cd backend && .venv/bin/python3 -m pytest tests/ -q`
Expected: same baseline as Task 1 Step 5 (only the pre-existing, unrelated `test_objects.py` failure remains).

- [ ] **Step 6: Commit**

```bash
git add backend/app/main.py backend/tests/test_main.py
git commit -m "feat: add /download/{image_id} endpoint for retrieving the original file"
```

---

## Deployment note (not a task — read before running this for real)

`ENABLE_WATCHER` defaults to `false`. On the actual server PC, set `ENABLE_WATCHER=true` (and optionally tune `RECONCILE_INTERVAL_SECONDS`, `WATCHER_STABLE_CHECK_SECONDS`) in the environment before starting the backend, or the real-time watching feature stays off even though the code is deployed.
