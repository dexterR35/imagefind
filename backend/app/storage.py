import json
import logging
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, fields
from pathlib import Path

import numpy as np
import sqlite_vec

from . import config

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS images (
    id TEXT PRIMARY KEY,
    path TEXT UNIQUE NOT NULL,
    filename TEXT NOT NULL DEFAULT '',
    thumbnail_path TEXT NOT NULL,
    ocr_text TEXT NOT NULL,
    objects TEXT NOT NULL CHECK (json_valid(objects)),
    mtime REAL NOT NULL,
    size INTEGER NOT NULL,
    width INTEGER NOT NULL DEFAULT 0,
    height INTEGER NOT NULL DEFAULT 0,
    format TEXT NOT NULL DEFAULT '',
    date_taken REAL NOT NULL DEFAULT 0,
    indexed_at REAL NOT NULL DEFAULT 0,
    embedding BLOB NOT NULL
);

CREATE TABLE IF NOT EXISTS image_objects (
    image_id TEXT NOT NULL REFERENCES images(id) ON UPDATE CASCADE ON DELETE CASCADE,
    label TEXT NOT NULL,
    PRIMARY KEY (image_id, label)
);

CREATE VIRTUAL TABLE IF NOT EXISTS image_fts USING fts5(
    filename,
    path,
    ocr_text,
    objects,
    tokenize='trigram'
);

CREATE TABLE IF NOT EXISTS index_store_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- User curation. These survive a reindex (an image keeps its id) and are wiped
-- only when the underlying image row is deleted (ON DELETE CASCADE).
CREATE TABLE IF NOT EXISTS favorites (
    image_id TEXT PRIMARY KEY REFERENCES images(id) ON UPDATE CASCADE ON DELETE CASCADE,
    created_at REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS collections (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    created_at REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS collection_images (
    collection_id TEXT NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    image_id TEXT NOT NULL REFERENCES images(id) ON UPDATE CASCADE ON DELETE CASCADE,
    added_at REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (collection_id, image_id)
);

CREATE TABLE IF NOT EXISTS image_user_tags (
    image_id TEXT NOT NULL REFERENCES images(id) ON UPDATE CASCADE ON DELETE CASCADE,
    tag TEXT NOT NULL,
    PRIMARY KEY (image_id, tag)
);

CREATE TABLE IF NOT EXISTS image_notes (
    image_id TEXT PRIMARY KEY REFERENCES images(id) ON UPDATE CASCADE ON DELETE CASCADE,
    note TEXT NOT NULL,
    updated_at REAL NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS images_date_taken_idx ON images(date_taken, id);
CREATE INDEX IF NOT EXISTS images_filename_idx ON images(filename COLLATE NOCASE, id);
CREATE INDEX IF NOT EXISTS images_size_idx ON images(size, id);
CREATE INDEX IF NOT EXISTS image_objects_label_idx ON image_objects(label, image_id);
CREATE INDEX IF NOT EXISTS collection_images_image_idx ON collection_images(image_id);
CREATE INDEX IF NOT EXISTS image_user_tags_tag_idx ON image_user_tags(tag, image_id);
"""

_METADATA_COLUMNS = [
    ("filename", "TEXT NOT NULL DEFAULT ''"),
    ("width", "INTEGER NOT NULL DEFAULT 0"),
    ("height", "INTEGER NOT NULL DEFAULT 0"),
    ("format", "TEXT NOT NULL DEFAULT ''"),
    ("date_taken", "REAL NOT NULL DEFAULT 0"),
    ("indexed_at", "REAL NOT NULL DEFAULT 0"),
]

_SELECT_COLUMNS = (
    "id, path, thumbnail_path, ocr_text, objects, mtime, size, "
    "width, height, format, date_taken, indexed_at"
)
_INSERT_COLUMNS = (
    "id, path, filename, thumbnail_path, ocr_text, objects, mtime, size, "
    "width, height, format, date_taken, indexed_at, embedding"
)
_PLACEHOLDERS = ", ".join("?" * 14)
_DATABASE_SCHEMA_VERSION = 5
_DERIVED_SCHEMA_VERSION = "4"

# A user picking "jpg" means either spelling; PIL stores "JPEG" but the suffix
# fallback in the indexer stores "JPG". Same idea for the tiff/heic variants.
_FORMAT_ALIASES = {
    "jpg": ("jpg", "jpeg"),
    "jpeg": ("jpg", "jpeg"),
    "tif": ("tif", "tiff"),
    "tiff": ("tif", "tiff"),
    "heic": ("heic", "heif"),
    "heif": ("heic", "heif"),
}
_DATE_FIELDS = ("date_taken", "mtime", "indexed_at")


@dataclass
class ImageEntry:
    id: str
    path: str
    thumbnail_path: str
    ocr_text: str
    objects: list[str]
    mtime: float
    size: int
    width: int = 0
    height: int = 0
    format: str = ""
    date_taken: float = 0.0
    indexed_at: float = 0.0


def _row_to_entry(row: tuple) -> ImageEntry:
    return ImageEntry(
        id=row[0], path=row[1], thumbnail_path=row[2], ocr_text=row[3],
        objects=json.loads(row[4]), mtime=row[5], size=row[6],
        width=row[7], height=row[8], format=row[9], date_taken=row[10],
        indexed_at=row[11],
    )


class IndexStore:
    """SQLite-backed image index.

    The database is the live query engine, not merely persistence for an
    in-memory copy. Metadata search uses ordinary indexed SQL/FTS5 and image
    similarity uses sqlite-vec's vec0 virtual table.
    """

    def __init__(self, index_dir: Path, embedding_dim: int = 512):
        self.index_dir = Path(index_dir)
        self.index_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.index_dir / "index.db"
        self.legacy_index_path = self.index_dir / "index.json"
        self.legacy_embeddings_path = self.index_dir / "embeddings.npy"
        self.embedding_dim = embedding_dim
        self.lock = threading.RLock()
        self._conn = self._open_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.enable_load_extension(True)
            try:
                sqlite_vec.load(conn)
            finally:
                conn.enable_load_extension(False)
            return conn
        except Exception:
            # On Windows an open failed connection still holds a file handle,
            # preventing _open_db() from replacing a corrupt database.
            conn.close()
            raise

    @staticmethod
    def _is_corruption_error(exc: sqlite3.DatabaseError) -> bool:
        code = getattr(exc, "sqlite_errorcode", None)
        if code in (sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB):
            return True
        message = str(exc).lower()
        return "malformed" in message or "not a database" in message

    def _quarantine_corrupt_database(self) -> None:
        timestamp = time.time_ns()
        for suffix in ("", "-wal", "-shm"):
            source = self.db_path.with_name(self.db_path.name + suffix)
            if source.exists():
                destination = source.with_name(source.name + f".corrupt-{timestamp}")
                source.replace(destination)

    def _initialize_schema(self, conn: sqlite3.Connection) -> None:
        # The original images table may predate the new columns, so it must be
        # created/migrated before the rest of the schema refers to filename.
        conn.execute(
            "CREATE TABLE IF NOT EXISTS images ("
            "id TEXT PRIMARY KEY, path TEXT UNIQUE NOT NULL, filename TEXT NOT NULL DEFAULT '', "
            "thumbnail_path TEXT NOT NULL, ocr_text TEXT NOT NULL, "
            "objects TEXT NOT NULL, mtime REAL NOT NULL, size INTEGER NOT NULL, "
            "width INTEGER NOT NULL DEFAULT 0, height INTEGER NOT NULL DEFAULT 0, "
            "format TEXT NOT NULL DEFAULT '', date_taken REAL NOT NULL DEFAULT 0, "
            "indexed_at REAL NOT NULL DEFAULT 0, embedding BLOB NOT NULL)"
        )
        self._migrate_add_metadata_columns(conn)
        self._migrate_remove_colors(conn)
        fts_columns = [
            row[1] for row in conn.execute("PRAGMA table_info(image_fts)").fetchall()
        ]
        expected_fts_columns = ["filename", "path", "ocr_text", "objects"]
        if fts_columns and fts_columns != expected_fts_columns:
            # FTS5 virtual tables cannot be ALTERed to add searchable fields.
            # Drop only this derived table; load() rebuilds it from images.
            conn.execute("DROP TABLE image_fts")
        conn.execute("DROP TRIGGER IF EXISTS images_delete_derived")
        conn.execute("DROP TABLE IF EXISTS image_colors")
        conn.executescript(_SCHEMA)
        vector_schema = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='image_vectors'"
        ).fetchone()
        expected_dimension = f"float[{self.embedding_dim}]"
        if vector_schema and expected_dimension not in vector_schema[0]:
            # Preserve image rows if a future embedding model changes
            # dimensions; only the derived vec0 index needs rebuilding.
            conn.execute("DROP TABLE image_vectors")
        conn.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS image_vectors USING vec0("
            f"embedding float[{self.embedding_dim}] distance_metric=cosine)"
        )
        conn.execute(
            "CREATE TRIGGER IF NOT EXISTS images_delete_derived AFTER DELETE ON images BEGIN "
            "DELETE FROM image_objects WHERE image_id=OLD.id; "
            "DELETE FROM image_fts WHERE rowid=OLD.rowid; "
            "DELETE FROM image_vectors WHERE rowid=OLD.rowid; "
            "DELETE FROM favorites WHERE image_id=OLD.id; "
            "DELETE FROM collection_images WHERE image_id=OLD.id; "
            "DELETE FROM image_user_tags WHERE image_id=OLD.id; "
            "DELETE FROM image_notes WHERE image_id=OLD.id; END"
        )
        conn.commit()

    def _open_db(self) -> sqlite3.Connection:
        try:
            conn = self._connect()
        except sqlite3.DatabaseError as exc:
            if not self._is_corruption_error(exc):
                raise
            logger.warning("IndexStore: %s is corrupt; quarantining it", self.db_path)
            self._quarantine_corrupt_database()
            conn = self._connect()
        try:
            self._initialize_schema(conn)
        except sqlite3.DatabaseError as exc:
            conn.close()
            if not self._is_corruption_error(exc):
                raise
            logger.warning("IndexStore: %s is corrupt; quarantining it", self.db_path)
            self._quarantine_corrupt_database()
            conn = self._connect()
            self._initialize_schema(conn)
        return conn

    @staticmethod
    def _migrate_add_metadata_columns(conn: sqlite3.Connection) -> None:
        existing = {row[1] for row in conn.execute("PRAGMA table_info(images)").fetchall()}
        for name, decl in _METADATA_COLUMNS:
            if name not in existing:
                conn.execute(f"ALTER TABLE images ADD COLUMN {name} {decl}")

    @staticmethod
    def _migrate_remove_colors(conn: sqlite3.Connection) -> None:
        """Discard legacy color metadata while preserving indexed images."""
        existing = {row[1] for row in conn.execute("PRAGMA table_info(images)").fetchall()}
        if "colors" not in existing:
            return
        conn.execute("DROP TRIGGER IF EXISTS images_delete_derived")
        conn.execute("DROP TABLE IF EXISTS image_colors")
        conn.execute("DROP TABLE IF EXISTS image_objects")
        conn.execute("DROP TABLE IF EXISTS image_fts")
        conn.execute("DROP TABLE IF EXISTS image_vectors")
        conn.execute("ALTER TABLE images RENAME TO images_with_colors")
        conn.execute(
            "CREATE TABLE images ("
            "id TEXT PRIMARY KEY, path TEXT UNIQUE NOT NULL, filename TEXT NOT NULL DEFAULT '', "
            "thumbnail_path TEXT NOT NULL, ocr_text TEXT NOT NULL, objects TEXT NOT NULL, "
            "mtime REAL NOT NULL, size INTEGER NOT NULL, width INTEGER NOT NULL DEFAULT 0, "
            "height INTEGER NOT NULL DEFAULT 0, format TEXT NOT NULL DEFAULT '', "
            "date_taken REAL NOT NULL DEFAULT 0, indexed_at REAL NOT NULL DEFAULT 0, "
            "embedding BLOB NOT NULL)"
        )
        conn.execute(
            "INSERT INTO images (rowid, id, path, filename, thumbnail_path, ocr_text, objects, "
            "mtime, size, width, height, format, date_taken, indexed_at, embedding) "
            "SELECT rowid, id, path, filename, thumbnail_path, ocr_text, objects, mtime, size, "
            "width, height, format, date_taken, indexed_at, embedding FROM images_with_colors"
        )
        conn.execute("DROP TABLE images_with_colors")

    def _entry_values(self, entry: ImageEntry, embedding: np.ndarray) -> tuple:
        vector = np.asarray(embedding, dtype=np.float32)
        if vector.shape != (self.embedding_dim,):
            raise ValueError(
                f"embedding for {entry.id!r} has shape {vector.shape}; "
                f"expected ({self.embedding_dim},)"
            )
        return (
            entry.id, entry.path, Path(entry.path).name, entry.thumbnail_path,
            entry.ocr_text, json.dumps(entry.objects), entry.mtime, entry.size,
            entry.width, entry.height, entry.format, entry.date_taken,
            entry.indexed_at, vector.tobytes(),
        )

    def _sync_derived(self, rowid: int, entry: ImageEntry, embedding: np.ndarray) -> None:
        self._conn.execute("DELETE FROM image_objects WHERE image_id=?", (entry.id,))
        self._conn.execute("DELETE FROM image_fts WHERE rowid=?", (rowid,))
        self._conn.execute("DELETE FROM image_vectors WHERE rowid=?", (rowid,))
        self._conn.executemany(
            "INSERT INTO image_objects(image_id, label) VALUES (?, ?)",
            [(entry.id, label) for label in sorted(set(entry.objects))],
        )
        self._conn.execute(
            "INSERT INTO image_fts(rowid, filename, path, ocr_text, objects) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                rowid,
                Path(entry.path).name,
                entry.path,
                entry.ocr_text,
                " ".join(entry.objects),
            ),
        )
        self._conn.execute(
            "INSERT INTO image_vectors(rowid, embedding) VALUES (?, ?)",
            (rowid, np.asarray(embedding, dtype=np.float32)),
        )

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
            logger.warning("IndexStore: legacy entries/embeddings length mismatch, skipping migration")
            return

        known_fields = {field.name for field in fields(ImageEntry)}
        entries: list[tuple[ImageEntry, np.ndarray]] = []
        try:
            for index, raw in enumerate(data):
                entry = ImageEntry(**{key: value for key, value in raw.items() if key in known_fields})
                vector = np.asarray(legacy_embeddings[index], dtype=np.float32)
                self._entry_values(entry, vector)
                entries.append((entry, vector))
        except (TypeError, ValueError, KeyError) as exc:
            logger.warning("IndexStore: legacy entry construction failed, skipping migration: %s", exc)
            return

        ids = [entry.id for entry, _ in entries]
        paths = [entry.path for entry, _ in entries]
        duplicate_count = (len(ids) - len(set(ids))) + (len(paths) - len(set(paths)))
        if duplicate_count:
            logger.warning("IndexStore: legacy index contains %d duplicate id/path value(s); last wins", duplicate_count)

        for entry, vector in entries:
            # Preserve the legacy INSERT OR REPLACE behavior for duplicate
            # ids and paths: later rows in index.json win deterministically.
            self._conn.execute(
                "DELETE FROM images WHERE id=? OR path=?", (entry.id, entry.path)
            )
            self.upsert(entry, vector)
        self._conn.execute("PRAGMA user_version = 1")
        self._conn.commit()

    def _derived_indexes_need_rebuild(self) -> bool:
        version_row = self._conn.execute(
            "SELECT value FROM index_store_meta WHERE key='derived_schema_version'"
        ).fetchone()
        version = f"{_DERIVED_SCHEMA_VERSION}:{self.embedding_dim}"
        if version_row != (version,):
            return True
        image_count = self._conn.execute("SELECT count(*) FROM images").fetchone()[0]
        vector_count = self._conn.execute("SELECT count(*) FROM image_vectors").fetchone()[0]
        fts_count = self._conn.execute("SELECT count(*) FROM image_fts").fetchone()[0]
        return image_count != vector_count or image_count != fts_count

    def _rebuild_derived_indexes(self) -> None:
        self._conn.execute("SAVEPOINT rebuild_derived_indexes")
        try:
            self._conn.execute("DELETE FROM image_objects")
            self._conn.execute("DELETE FROM image_fts")
            self._conn.execute("DELETE FROM image_vectors")
            rows = self._conn.execute(
                f"SELECT rowid, {_SELECT_COLUMNS}, embedding FROM images ORDER BY rowid"
            ).fetchall()
            for row in rows:
                rowid = row[0]
                try:
                    entry = _row_to_entry(row[1:13])
                    vector = np.frombuffer(row[13], dtype=np.float32)
                    if vector.shape != (self.embedding_dim,):
                        raise ValueError(
                            f"embedding shape {vector.shape}; expected ({self.embedding_dim},)"
                        )
                except (json.JSONDecodeError, TypeError, ValueError) as exc:
                    # A single malformed primary row is recoverable from its
                    # source image. Do not discard the rest of a large index.
                    logger.warning("dropping unreadable image rowid %s: %s", rowid, exc)
                    self._conn.execute("DELETE FROM images WHERE rowid=?", (rowid,))
                    continue
                self._conn.execute(
                    "UPDATE images SET filename=? WHERE rowid=?",
                    (Path(entry.path).name, rowid),
                )
                self._sync_derived(rowid, entry, vector)
            self._conn.execute(
                "INSERT OR REPLACE INTO index_store_meta(key, value) "
                "VALUES ('derived_schema_version', ?)",
                (f"{_DERIVED_SCHEMA_VERSION}:{self.embedding_dim}",),
            )
            self._conn.execute("RELEASE SAVEPOINT rebuild_derived_indexes")
            self._conn.commit()
        except Exception:
            self._conn.execute("ROLLBACK TO SAVEPOINT rebuild_derived_indexes")
            self._conn.execute("RELEASE SAVEPOINT rebuild_derived_indexes")
            raise

    def load(self) -> None:
        with self.lock:
            user_version = self._conn.execute("PRAGMA user_version").fetchone()[0]
            if self.legacy_index_path.exists():
                if user_version == 0:
                    self._migrate_from_legacy_files()
            if self._derived_indexes_need_rebuild():
                self._rebuild_derived_indexes()
            self._conn.execute(f"PRAGMA user_version={_DATABASE_SCHEMA_VERSION}")
            self._conn.commit()

    def save(self) -> None:
        with self.lock:
            self._conn.commit()

    def needs_reindex(self, path: Path) -> bool:
        entry = self.get_by_path(str(path))
        if entry is None:
            return True
        stat = path.stat()
        metadata_missing = (
            entry.width <= 0 or entry.height <= 0 or not entry.format
            or entry.date_taken <= 0 or entry.indexed_at <= 0
        )
        mtime_changed = abs(entry.mtime - stat.st_mtime) > config.MTIME_TOLERANCE_SECONDS
        return metadata_missing or mtime_changed or entry.size != stat.st_size

    def upsert(self, entry: ImageEntry, embedding: np.ndarray) -> None:
        with self.lock:
            # Keep reindex writes batched until save(), as before, while the
            # nested savepoint guarantees one bad derived-index write cannot
            # leave a half-updated primary row in that batch.
            if not self._conn.in_transaction:
                self._conn.execute("BEGIN")
            self._conn.execute("SAVEPOINT upsert_image")
            try:
                values = self._entry_values(entry, embedding)
                row = self._conn.execute(
                    f"INSERT INTO images ({_INSERT_COLUMNS}) VALUES ({_PLACEHOLDERS}) "
                    "ON CONFLICT(path) DO UPDATE SET "
                    "id=excluded.id, filename=excluded.filename, thumbnail_path=excluded.thumbnail_path, "
                    "ocr_text=excluded.ocr_text, objects=excluded.objects, "
                    "mtime=excluded.mtime, size=excluded.size, width=excluded.width, height=excluded.height, "
                    "format=excluded.format, date_taken=excluded.date_taken, indexed_at=excluded.indexed_at, "
                    "embedding=excluded.embedding RETURNING rowid",
                    values,
                ).fetchone()
                self._sync_derived(row[0], entry, embedding)
                self._conn.execute("RELEASE SAVEPOINT upsert_image")
            except Exception:
                self._conn.execute("ROLLBACK TO SAVEPOINT upsert_image")
                self._conn.execute("RELEASE SAVEPOINT upsert_image")
                raise

    def prune(self, keep_paths: set[str]) -> list[str]:
        with self.lock:
            self._conn.execute("SAVEPOINT prune_images")
            try:
                self._conn.execute("CREATE TEMP TABLE IF NOT EXISTS keep_paths(path TEXT PRIMARY KEY)")
                self._conn.execute("DELETE FROM keep_paths")
                self._conn.executemany(
                    "INSERT INTO keep_paths(path) VALUES (?)", [(path,) for path in keep_paths]
                )
                removed_thumbnails = [
                    row[0]
                    for row in self._conn.execute(
                        "SELECT thumbnail_path FROM images "
                        "WHERE path NOT IN (SELECT path FROM keep_paths)"
                    ).fetchall()
                ]
                self._conn.execute(
                    "DELETE FROM images WHERE path NOT IN (SELECT path FROM keep_paths)"
                )
                self._conn.execute("DROP TABLE keep_paths")
                self._conn.execute("RELEASE SAVEPOINT prune_images")
                self._conn.commit()
                return removed_thumbnails
            except Exception:
                self._conn.execute("ROLLBACK TO SAVEPOINT prune_images")
                self._conn.execute("RELEASE SAVEPOINT prune_images")
                raise

    def get(self, image_id: str) -> ImageEntry | None:
        with self.lock:
            row = self._conn.execute(
                f"SELECT {_SELECT_COLUMNS} FROM images WHERE id=?", (image_id,)
            ).fetchone()
            return _row_to_entry(row) if row else None

    def get_by_path(self, path: str) -> ImageEntry | None:
        with self.lock:
            row = self._conn.execute(
                f"SELECT {_SELECT_COLUMNS} FROM images WHERE path=?", (path,)
            ).fetchone()
            return _row_to_entry(row) if row else None

    def get_embedding(self, image_id: str) -> np.ndarray | None:
        with self.lock:
            row = self._conn.execute("SELECT embedding FROM images WHERE id=?", (image_id,)).fetchone()
            return np.frombuffer(row[0], dtype=np.float32).copy() if row else None

    def all(self) -> list[ImageEntry]:
        with self.lock:
            return [
                _row_to_entry(row)
                for row in self._conn.execute(
                    f"SELECT {_SELECT_COLUMNS} FROM images ORDER BY rowid"
                ).fetchall()
            ]

    def all_paths(self) -> set[str]:
        with self.lock:
            return {row[0] for row in self._conn.execute("SELECT path FROM images")}

    def all_thumbnail_paths(self) -> set[str]:
        with self.lock:
            return {
                row[0] for row in self._conn.execute("SELECT thumbnail_path FROM images")
            }

    @property
    def entries(self) -> list[ImageEntry]:
        # Compatibility for maintenance/tests; normal application paths use
        # count/search/get and do not materialize the catalog.
        return self.all()

    @property
    def embeddings(self) -> np.ndarray:
        with self.lock:
            rows = self._conn.execute("SELECT embedding FROM images ORDER BY rowid").fetchall()
        return (
            np.vstack([np.frombuffer(row[0], dtype=np.float32) for row in rows])
            if rows else np.zeros((0, self.embedding_dim), dtype=np.float32)
        )

    @property
    def _by_id(self) -> dict[str, int]:
        return {entry.id: index for index, entry in enumerate(self.all())}

    def count(self) -> int:
        with self.lock:
            return self._conn.execute("SELECT count(*) FROM images").fetchone()[0]

    def close(self) -> None:
        with self.lock:
            self._conn.close()

    def backup(self, destination: Path) -> None:
        """Write a fully consistent, defragmented copy of the committed
        database to ``destination``.

        ``VACUUM INTO`` produces a standalone file (no ``-wal``/``-shm``
        companions). It runs on a throwaway read connection so it neither
        disturbs the live connection's batching transaction nor requires a
        write lock — searches keep serving while the copy is written.
        """
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with self.lock:
            reader = sqlite3.connect(str(self.db_path), timeout=5)
            try:
                reader.execute("VACUUM INTO ?", (str(destination),))
            finally:
                reader.close()

    def distinct_objects(self) -> list[str]:
        with self.lock:
            return [row[0] for row in self._conn.execute("SELECT DISTINCT label FROM image_objects ORDER BY label")]

    def stats(self, largest: int = 10) -> dict:
        """Catalog-wide aggregates for the stats dashboard, all computed in SQL."""
        with self.lock:
            conn = self._conn
            total, total_size, indexed_min, indexed_max = conn.execute(
                "SELECT count(*), coalesce(sum(size), 0), "
                "min(nullif(indexed_at, 0)), max(indexed_at) FROM images"
            ).fetchone()
            by_format = [
                {"format": (row[0] or "").upper() or "UNKNOWN", "count": row[1]}
                for row in conn.execute(
                    "SELECT lower(format), count(*) FROM images "
                    "GROUP BY lower(format) ORDER BY count(*) DESC, lower(format)"
                )
            ]
            by_year = [
                {"year": row[0], "count": row[1]}
                for row in conn.execute(
                    "SELECT CASE WHEN date_taken > 0 "
                    "THEN strftime('%Y', date_taken, 'unixepoch') ELSE 'Unknown' END AS year, "
                    "count(*) FROM images GROUP BY year ORDER BY year DESC"
                )
            ]
            with_ocr = conn.execute(
                "SELECT count(*) FROM images WHERE trim(ocr_text) != ''"
            ).fetchone()[0]
            with_objects = conn.execute(
                "SELECT count(*) FROM images WHERE json_array_length(objects) > 0"
            ).fetchone()[0]
            without_any = conn.execute(
                "SELECT count(*) FROM images "
                "WHERE trim(ocr_text) = '' AND json_array_length(objects) = 0"
            ).fetchone()[0]
            biggest = [
                {"id": row[0], "path": row[1], "size": row[2]}
                for row in conn.execute(
                    "SELECT id, path, size FROM images ORDER BY size DESC, id LIMIT ?",
                    (max(0, largest),),
                )
            ]
        return {
            "total": total,
            "total_size": total_size,
            "indexed_at_min": indexed_min,
            "indexed_at_max": indexed_max,
            "by_format": by_format,
            "by_year": by_year,
            "with_ocr_text": with_ocr,
            "with_objects": with_objects,
            "without_ocr_or_objects": without_any,
            "largest": biggest,
        }

    # ---- User curation: favorites, manual tags, notes ---------------------

    def _existing_ids(self, image_ids: list[str]) -> list[str]:
        """Keep only ids that still have an images row — a bulk action on a
        stale selection must not raise a foreign-key error for the whole batch."""
        ids = list(dict.fromkeys(image_ids))
        if not ids:
            return []
        placeholders = ", ".join("?" * len(ids))
        present = {
            row[0]
            for row in self._conn.execute(
                f"SELECT id FROM images WHERE id IN ({placeholders})", ids
            )
        }
        return [image_id for image_id in ids if image_id in present]

    def set_favorite(self, image_id: str, favorite: bool) -> bool:
        self.set_favorites([image_id], favorite)
        return favorite

    def set_favorites(self, image_ids: list[str], favorite: bool) -> int:
        with self.lock:
            ids = self._existing_ids(image_ids)
            if not ids:
                return 0
            if favorite:
                now = time.time()
                cursor = self._conn.executemany(
                    "INSERT OR IGNORE INTO favorites(image_id, created_at) VALUES (?, ?)",
                    [(image_id, now) for image_id in ids],
                )
            else:
                placeholders = ", ".join("?" * len(ids))
                cursor = self._conn.execute(
                    f"DELETE FROM favorites WHERE image_id IN ({placeholders})", ids
                )
            self._conn.commit()
            return cursor.rowcount

    def add_user_tags(self, image_ids: list[str], tags: list[str]) -> int:
        """Add each tag to each image (idempotent). Unlike set_user_tags this
        does not clear existing tags — it is the bulk-select 'tag these' action."""
        cleaned = sorted({tag.strip() for tag in tags if tag.strip()}, key=str.lower)
        if not cleaned:
            return 0
        with self.lock:
            ids = self._existing_ids(image_ids)
            if not ids:
                return 0
            cursor = self._conn.executemany(
                "INSERT OR IGNORE INTO image_user_tags(image_id, tag) VALUES (?, ?)",
                [(image_id, tag) for image_id in ids for tag in cleaned],
            )
            self._conn.commit()
            return cursor.rowcount

    def set_user_tags(self, image_id: str, tags: list[str]) -> list[str]:
        cleaned = sorted({tag.strip() for tag in tags if tag.strip()}, key=str.lower)
        with self.lock:
            self._conn.execute("DELETE FROM image_user_tags WHERE image_id=?", (image_id,))
            self._conn.executemany(
                "INSERT INTO image_user_tags(image_id, tag) VALUES (?, ?)",
                [(image_id, tag) for tag in cleaned],
            )
            self._conn.commit()
        return cleaned

    def set_note(self, image_id: str, note: str) -> str:
        note = note.strip()
        with self.lock:
            if note:
                self._conn.execute(
                    "INSERT INTO image_notes(image_id, note, updated_at) VALUES (?, ?, ?) "
                    "ON CONFLICT(image_id) DO UPDATE SET "
                    "note=excluded.note, updated_at=excluded.updated_at",
                    (image_id, note, time.time()),
                )
            else:
                self._conn.execute("DELETE FROM image_notes WHERE image_id=?", (image_id,))
            self._conn.commit()
        return note

    def distinct_user_tags(self) -> list[str]:
        with self.lock:
            return [
                row[0]
                for row in self._conn.execute(
                    "SELECT DISTINCT tag FROM image_user_tags ORDER BY tag COLLATE NOCASE"
                )
            ]

    def get_annotations(self, image_ids: list[str]) -> dict[str, dict]:
        """Favorite flag, user tags and note for a batch of images — one query
        per table, so a page of results costs 3 statements, not 3N."""
        ids = list(dict.fromkeys(image_ids))
        if not ids:
            return {}
        placeholders = ", ".join("?" * len(ids))
        with self.lock:
            favorites = {
                row[0]
                for row in self._conn.execute(
                    f"SELECT image_id FROM favorites WHERE image_id IN ({placeholders})", ids
                )
            }
            tags: dict[str, list[str]] = {}
            for image_id, tag in self._conn.execute(
                f"SELECT image_id, tag FROM image_user_tags "
                f"WHERE image_id IN ({placeholders}) ORDER BY tag COLLATE NOCASE",
                ids,
            ):
                tags.setdefault(image_id, []).append(tag)
            notes = dict(
                self._conn.execute(
                    f"SELECT image_id, note FROM image_notes WHERE image_id IN ({placeholders})",
                    ids,
                )
            )
        return {
            image_id: {
                "favorite": image_id in favorites,
                "user_tags": tags.get(image_id, []),
                "note": notes.get(image_id, ""),
            }
            for image_id in ids
        }

    # ---- Collections ----------------------------------------------------

    def list_collections(self) -> list[dict]:
        with self.lock:
            return [
                {"id": row[0], "name": row[1], "created_at": row[2], "count": row[3]}
                for row in self._conn.execute(
                    "SELECT c.id, c.name, c.created_at, "
                    "(SELECT count(*) FROM collection_images ci WHERE ci.collection_id=c.id) "
                    "FROM collections c ORDER BY c.name COLLATE NOCASE"
                )
            ]

    def create_collection(self, name: str) -> dict:
        name = name.strip()
        if not name:
            raise ValueError("collection name must not be empty")
        collection_id = uuid.uuid4().hex
        now = time.time()
        with self.lock:
            try:
                self._conn.execute(
                    "INSERT INTO collections(id, name, created_at) VALUES (?, ?, ?)",
                    (collection_id, name, now),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError(f"a collection named {name!r} already exists") from exc
            self._conn.commit()
        return {"id": collection_id, "name": name, "created_at": now, "count": 0}

    def rename_collection(self, collection_id: str, name: str) -> bool:
        name = name.strip()
        if not name:
            raise ValueError("collection name must not be empty")
        with self.lock:
            try:
                cursor = self._conn.execute(
                    "UPDATE collections SET name=? WHERE id=?", (name, collection_id)
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError(f"a collection named {name!r} already exists") from exc
            self._conn.commit()
            return cursor.rowcount > 0

    def delete_collection(self, collection_id: str) -> bool:
        with self.lock:
            cursor = self._conn.execute(
                "DELETE FROM collections WHERE id=?", (collection_id,)
            )
            self._conn.commit()
            return cursor.rowcount > 0

    def collection_exists(self, collection_id: str) -> bool:
        with self.lock:
            return (
                self._conn.execute(
                    "SELECT 1 FROM collections WHERE id=?", (collection_id,)
                ).fetchone()
                is not None
            )

    def add_to_collection(self, collection_id: str, image_ids: list[str]) -> int:
        now = time.time()
        with self.lock:
            if not self._conn.execute(
                "SELECT 1 FROM collections WHERE id=?", (collection_id,)
            ).fetchone():
                raise KeyError(collection_id)
            ids = self._existing_ids(image_ids)
            if not ids:
                return 0
            cursor = self._conn.executemany(
                "INSERT OR IGNORE INTO collection_images(collection_id, image_id, added_at) "
                "VALUES (?, ?, ?)",
                [(collection_id, image_id, now) for image_id in ids],
            )
            self._conn.commit()
            return cursor.rowcount

    def remove_from_collection(self, collection_id: str, image_ids: list[str]) -> int:
        ids = list(dict.fromkeys(image_ids))
        if not ids:
            return 0
        placeholders = ", ".join("?" * len(ids))
        with self.lock:
            cursor = self._conn.execute(
                f"DELETE FROM collection_images "
                f"WHERE collection_id=? AND image_id IN ({placeholders})",
                [collection_id, *ids],
            )
            self._conn.commit()
            return cursor.rowcount

    def search(
        self,
        text: str | None = None,
        obj: str | None = None,
        fmt: str | None = None,
        size_min: int | None = None,
        size_max: int | None = None,
        date_field: str = "date_taken",
        date_from: float | None = None,
        date_to: float | None = None,
        width_min: int | None = None,
        width_max: int | None = None,
        height_min: int | None = None,
        height_max: int | None = None,
        orientation: str | None = None,
        favorite: bool | None = None,
        collection: str | None = None,
        user_tag: str | None = None,
        sort: str = "date_desc",
        offset: int = 0,
        limit: int = 60,
    ) -> tuple[list[ImageEntry], int]:
        clauses: list[str] = []
        params: list[object] = []
        # Each word of the query (3+ chars) becomes its own quote-escaped FTS5
        # phrase, AND-ed together. Joining the FTS index also exposes bm25
        # `rank` to ORDER BY
        # so the strongest matches, not just the newest, come first.
        fts_terms = [term for term in text.split() if len(term) >= 3] if text else []
        rank_by_relevance = bool(fts_terms)
        # A one-word query is where the trigram tokenizer's substring recall
        # hurts most - "cat" also matches inside "communication". Whole-word
        # hits are floated above substring-only hits (both are still returned).
        single_token = (
            len(fts_terms) == 1 and text.strip().lower() == fts_terms[0].lower()
        )
        fts_join = " JOIN image_fts ON image_fts.rowid = images.rowid" if rank_by_relevance else ""
        if rank_by_relevance:
            clauses.append("image_fts MATCH ?")
            params.append(" AND ".join('"' + term.replace('"', '""') + '"' for term in fts_terms))
        if obj:
            clauses.append("EXISTS (SELECT 1 FROM image_objects o WHERE o.image_id=images.id AND o.label=?)")
            params.append(obj)
        if text and not rank_by_relevance:
            clauses.append(
                "(instr(lower(images.filename), lower(?)) > 0 "
                "OR instr(lower(images.path), lower(?)) > 0 "
                "OR instr(lower(images.ocr_text), lower(?)) > 0 OR EXISTS ("
                "SELECT 1 FROM image_objects o WHERE o.image_id=images.id "
                "AND instr(lower(o.label), lower(?)) > 0))"
            )
            params.extend((text, text, text, text))

        # Metadata facets. Each is an independent, indexed comparison on the
        # images row and AND-s with everything above.
        if fmt:
            spellings = _FORMAT_ALIASES.get(fmt.lower(), (fmt.lower(),))
            placeholders = ", ".join("?" * len(spellings))
            clauses.append(f"lower(images.format) IN ({placeholders})")
            params.extend(spellings)
        if size_min is not None:
            clauses.append("images.size >= ?")
            params.append(size_min)
        if size_max is not None:
            clauses.append("images.size <= ?")
            params.append(size_max)
        date_column = date_field if date_field in _DATE_FIELDS else "date_taken"
        if date_from is not None:
            clauses.append(f"images.{date_column} >= ?")
            params.append(date_from)
        if date_to is not None:
            clauses.append(f"images.{date_column} <= ?")
            params.append(date_to)
        for value, column, op in (
            (width_min, "width", ">="),
            (width_max, "width", "<="),
            (height_min, "height", ">="),
            (height_max, "height", "<="),
        ):
            if value is not None:
                clauses.append(f"images.{column} {op} ?")
                params.append(value)
        if orientation == "portrait":
            clauses.append("images.height > images.width")
        elif orientation == "landscape":
            clauses.append("images.width > images.height")
        elif orientation == "square":
            clauses.append("images.width = images.height AND images.width > 0")
        if favorite:
            clauses.append("EXISTS (SELECT 1 FROM favorites f WHERE f.image_id=images.id)")
        if collection:
            clauses.append(
                "EXISTS (SELECT 1 FROM collection_images ci "
                "WHERE ci.image_id=images.id AND ci.collection_id=?)"
            )
            params.append(collection)
        if user_tag:
            clauses.append(
                "EXISTS (SELECT 1 FROM image_user_tags ut "
                "WHERE ut.image_id=images.id AND ut.tag=?)"
            )
            params.append(user_tag)

        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        # Columns must be qualified: the FTS join brings its own path/filename/
        # ocr_text columns that would otherwise make the SELECT ambiguous.
        select_cols = ", ".join(f"images.{col.strip()}" for col in _SELECT_COLUMNS.split(","))
        base_order = {
            "date_desc": "images.date_taken DESC",
            "date_asc": "images.date_taken ASC",
            "name_asc": "images.filename COLLATE NOCASE ASC",
            "name_desc": "images.filename COLLATE NOCASE DESC",
            "size_desc": "images.size DESC",
            "size_asc": "images.size ASC",
        }.get(sort, "images.date_taken DESC")
        order_params: list[object] = []
        if rank_by_relevance and sort == "date_desc":
            # "date_desc" is also the default the frontend sends when the user
            # has picked no explicit order, so a text search leaves relevance
            # first; any other explicit sort wins and relevance breaks ties.
            relevance = "image_fts.rank"
            if single_token:
                needle = fts_terms[0].lower()
                # 0 = query appears as a standalone word in any searchable
                # field (path separators normalised to spaces first), 1 = it
                # only occurs as a substring of a longer token.
                relevance = (
                    "(CASE WHEN "
                    "instr(' ' || lower(images.ocr_text) || ' ', ' ' || ? || ' ') > 0 "
                    "OR instr(' ' || replace(replace(replace(replace(replace("
                    "lower(images.path), '/', ' '), '\\', ' '), '_', ' '), '-', ' '), '.', ' ') "
                    "|| ' ', ' ' || ? || ' ') > 0 "
                    "OR EXISTS (SELECT 1 FROM image_objects o WHERE o.image_id = images.id "
                    "AND instr(' ' || lower(o.label) || ' ', ' ' || ? || ' ') > 0) "
                    "THEN 0 ELSE 1 END), image_fts.rank"
                )
                order_params = [needle, needle, needle]
            order_by = f"{relevance}, {base_order}, images.id ASC"
        elif rank_by_relevance:
            order_by = f"{base_order}, image_fts.rank, images.id ASC"
        else:
            order_by = f"{base_order}, images.id ASC"

        with self.lock:
            total = self._conn.execute(
                f"SELECT count(*) FROM images{fts_join}{where}", params
            ).fetchone()[0]
            rows = self._conn.execute(
                f"SELECT {select_cols} FROM images{fts_join}{where} "
                f"ORDER BY {order_by} LIMIT ? OFFSET ?",
                [*params, *order_params, limit, offset],
            ).fetchall()
        return [_row_to_entry(row) for row in rows], total

    def search_semantic(self, embedding: np.ndarray, limit: int = 60) -> list[ImageEntry]:
        """CLIP text→image search: nearest images to a query-text embedding,
        ranked by cosine distance. Metadata facets do not apply (like
        find_similar, this is a pure vector ranking)."""
        vector = np.asarray(embedding, dtype=np.float32)
        if vector.shape != (self.embedding_dim,):
            raise ValueError(
                f"query embedding has shape {vector.shape}; expected ({self.embedding_dim},)"
            )
        if limit <= 0:
            return []
        with self.lock:
            rows = self._conn.execute(
                "WITH nearest AS MATERIALIZED ("
                "SELECT rowid, distance FROM image_vectors "
                "WHERE embedding MATCH ? AND k=? ORDER BY distance"
                ") SELECT " + _SELECT_COLUMNS + " FROM nearest "
                "JOIN images ON images.rowid=nearest.rowid "
                "ORDER BY nearest.distance, nearest.rowid LIMIT ?",
                (vector, limit, limit),
            ).fetchall()
        return [_row_to_entry(row) for row in rows]

    def find_duplicate_groups(
        self, threshold: float = 0.08, max_images: int = 5000, max_groups: int = 50
    ) -> list[list[ImageEntry]]:
        """Cluster visually near-identical images by CLIP cosine distance.

        Each image is unioned with its <= k nearest neighbours that fall within
        ``threshold`` cosine distance (0 = identical). Only clusters of two or
        more are returned, largest first.
        """
        with self.lock:
            rowids = [
                row[0]
                for row in self._conn.execute(
                    "SELECT rowid FROM images ORDER BY rowid LIMIT ?", (max(1, max_images),)
                )
            ]
            if len(rowids) < 2:
                return []
            parent = {rowid: rowid for rowid in rowids}

            def find(node: int) -> int:
                while parent[node] != node:
                    parent[node] = parent[parent[node]]
                    node = parent[node]
                return node

            def union(a: int, b: int) -> None:
                root_a, root_b = find(a), find(b)
                if root_a != root_b:
                    parent[root_a] = root_b

            in_scope = set(rowids)
            for rowid in rowids:
                neighbours = self._conn.execute(
                    "SELECT rowid, distance FROM image_vectors "
                    "WHERE embedding MATCH (SELECT embedding FROM image_vectors WHERE rowid=?) "
                    "AND k=? ORDER BY distance",
                    (rowid, 6),
                ).fetchall()
                for neighbour_id, distance in neighbours:
                    if neighbour_id != rowid and neighbour_id in in_scope and distance <= threshold:
                        union(rowid, neighbour_id)

            clusters: dict[int, list[int]] = {}
            for rowid in rowids:
                clusters.setdefault(find(rowid), []).append(rowid)
            groups = sorted(
                (members for members in clusters.values() if len(members) >= 2),
                key=len,
                reverse=True,
            )[: max(0, max_groups)]

            result: list[list[ImageEntry]] = []
            for members in groups:
                placeholders = ", ".join("?" * len(members))
                rows = self._conn.execute(
                    f"SELECT {_SELECT_COLUMNS} FROM images "
                    f"WHERE rowid IN ({placeholders}) ORDER BY size DESC, id",
                    members,
                ).fetchall()
                result.append([_row_to_entry(row) for row in rows])
            return result

    def find_similar(self, image_id: str, limit: int = 20) -> list[ImageEntry] | None:
        with self.lock:
            row = self._conn.execute("SELECT rowid FROM images WHERE id=?", (image_id,)).fetchone()
            if row is None:
                return None
            if limit <= 0:
                return []
            rowid = row[0]
            rows = self._conn.execute(
                "WITH nearest AS MATERIALIZED ("
                "SELECT rowid, distance FROM image_vectors "
                "WHERE embedding MATCH (SELECT embedding FROM image_vectors WHERE rowid=?) "
                "AND k=? ORDER BY distance"
                ") SELECT " + _SELECT_COLUMNS + " FROM nearest "
                "JOIN images ON images.rowid=nearest.rowid "
                "WHERE nearest.rowid != ? ORDER BY nearest.distance, nearest.rowid LIMIT ?",
                (rowid, limit + 1, rowid, limit),
            ).fetchall()
            return [_row_to_entry(result) for result in rows]

    def delete_by_path(self, path: str) -> str | None:
        with self.lock:
            row = self._conn.execute(
                "SELECT thumbnail_path FROM images WHERE path=?", (path,)
            ).fetchone()
            self._conn.execute("DELETE FROM images WHERE path=?", (path,))
            self._conn.commit()
            return row[0] if row else None

    def delete_under_directory(self, directory: str) -> list[str]:
        """Delete every indexed image below a directory path.

        Both separators are accepted because an index may have been created
        on a different OS, or a network path may have been normalized by an
        external caller. Exact prefix comparison avoids treating ``%`` and
        ``_`` in real folder names as SQL LIKE wildcards.
        """
        base = directory.rstrip("\\/")
        prefixes = (base + "\\", base + "/")
        predicate = (
            "path = ? COLLATE NOCASE OR "
            "substr(path, 1, ?) = ? COLLATE NOCASE OR "
            "substr(path, 1, ?) = ? COLLATE NOCASE"
        )
        params = (base, len(prefixes[0]), prefixes[0], len(prefixes[1]), prefixes[1])
        with self.lock:
            removed_thumbnails = [
                row[0]
                for row in self._conn.execute(
                    f"SELECT thumbnail_path FROM images WHERE {predicate}", params
                ).fetchall()
            ]
            self._conn.execute(f"DELETE FROM images WHERE {predicate}", params)
            self._conn.commit()
            return removed_thumbnails
