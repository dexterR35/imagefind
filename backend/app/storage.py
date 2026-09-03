import json
import logging
import sqlite3
import threading
import time
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

CREATE INDEX IF NOT EXISTS images_date_taken_idx ON images(date_taken, id);
CREATE INDEX IF NOT EXISTS images_filename_idx ON images(filename COLLATE NOCASE, id);
CREATE INDEX IF NOT EXISTS images_size_idx ON images(size, id);
CREATE INDEX IF NOT EXISTS image_objects_label_idx ON image_objects(label, image_id);
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
_DATABASE_SCHEMA_VERSION = 4
_DERIVED_SCHEMA_VERSION = "4"


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
            "DELETE FROM image_vectors WHERE rowid=OLD.rowid; END"
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

    def distinct_objects(self) -> list[str]:
        with self.lock:
            return [row[0] for row in self._conn.execute("SELECT DISTINCT label FROM image_objects ORDER BY label")]

    def search(
        self,
        text: str | None = None,
        obj: str | None = None,
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
