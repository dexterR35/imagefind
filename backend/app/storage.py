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
        try:
            conn.execute("PRAGMA journal_mode=WAL")
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

    def _migrate_from_legacy_files(self) -> None:
        try:
            data = json.loads(self.legacy_index_path.read_text())
        except (OSError, json.JSONDecodeError):
            logger.warning("IndexStore: legacy index.json unreadable, skipping migration")
            return
        try:
            legacy_embeddings = np.load(self.legacy_embeddings_path)
        except (OSError, ValueError):
            logger.warning("IndexStore: legacy embeddings.npy unreadable, skipping migration")
            return
        if len(data) != legacy_embeddings.shape[0]:
            logger.warning(
                "IndexStore: legacy entries/embeddings length mismatch, skipping migration"
            )
            return

        known_fields = {f.name for f in fields(ImageEntry)}
        rows = []
        try:
            for i, e in enumerate(data):
                entry = ImageEntry(**{k: v for k, v in e.items() if k in known_fields})
                rows.append((
                    entry.id, entry.path, entry.thumbnail_path, entry.ocr_text,
                    json.dumps(entry.colors), json.dumps(entry.objects),
                    entry.mtime, entry.size,
                    legacy_embeddings[i].astype(np.float32).tobytes(),
                ))
        except (TypeError, ValueError, KeyError) as exc:
            logger.warning("IndexStore: legacy entry construction failed, skipping migration: %s", exc)
            return
        self._conn.executemany(
            "INSERT OR REPLACE INTO images "
            "(id, path, thumbnail_path, ocr_text, colors, objects, mtime, size, embedding) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        self._conn.commit()

    def load(self) -> None:
        with self.lock:
            if self.legacy_index_path.exists():
                count = self._conn.execute("SELECT COUNT(*) FROM images").fetchone()[0]
                if count == 0:
                    self._migrate_from_legacy_files()

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
