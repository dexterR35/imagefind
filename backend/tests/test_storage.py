import json
import logging
import sqlite3
import threading

import numpy as np
import pytest

from app.storage import ImageEntry, IndexStore


def _entry(path="/imgs/a.png", mtime=0.0, size=0, id="a1", **kwargs):
    return ImageEntry(
        id=id, path=path, thumbnail_path=f"/thumbs/{id}.jpg",
        ocr_text="NETBET", colors=["green"], objects=["clover"],
        mtime=mtime, size=size, **kwargs,
    )


def test_upsert_save_load_roundtrip(tmp_path):
    store = IndexStore(tmp_path, embedding_dim=4)
    store.load()
    store.upsert(_entry(), np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32))
    store.save()

    reloaded = IndexStore(tmp_path, embedding_dim=4)
    reloaded.load()
    assert reloaded.get("a1").ocr_text == "NETBET"
    assert reloaded.get_embedding("a1").tolist() == [1.0, 0.0, 0.0, 0.0]
    assert reloaded.get_by_path("/imgs/a.png").id == "a1"
    assert reloaded._conn.execute("PRAGMA user_version").fetchone()[0] == 3


def test_upsert_save_load_roundtrip_preserves_new_metadata_fields(tmp_path):
    store = IndexStore(tmp_path, embedding_dim=4)
    store.load()
    store.upsert(
        _entry(width=1920, height=1080, format="PNG", date_taken=111.0, indexed_at=222.0),
        np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32),
    )
    store.save()

    reloaded = IndexStore(tmp_path, embedding_dim=4)
    reloaded.load()
    entry = reloaded.get("a1")
    assert (entry.width, entry.height, entry.format) == (1920, 1080, "PNG")
    assert (entry.date_taken, entry.indexed_at) == (111.0, 222.0)


def test_opening_a_pre_metadata_schema_db_migrates_columns_without_losing_data(tmp_path):
    # Simulates an index.db created before width/height/format/date_taken/
    # indexed_at existed - IndexStore must ALTER the table in place rather
    # than choking on (or silently dropping) the pre-existing row.
    db_path = tmp_path / "index.db"
    tmp_path.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE images ("
        "id TEXT PRIMARY KEY, path TEXT UNIQUE NOT NULL, thumbnail_path TEXT NOT NULL, "
        "ocr_text TEXT NOT NULL, colors TEXT NOT NULL, objects TEXT NOT NULL, "
        "mtime REAL NOT NULL, size INTEGER NOT NULL, embedding BLOB NOT NULL)"
    )
    conn.execute(
        "INSERT INTO images (id, path, thumbnail_path, ocr_text, colors, objects, mtime, size, embedding) "
        "VALUES ('a1', '/imgs/a.png', '/thumbs/a1.jpg', 'NETBET', '[\"green\"]', '[\"clover\"]', "
        "0.0, 0, ?)",
        (np.zeros(4, dtype=np.float32).tobytes(),),
    )
    conn.commit()
    conn.close()

    store = IndexStore(tmp_path, embedding_dim=4)
    store.load()

    entry = store.get("a1")
    assert entry.ocr_text == "NETBET"
    assert (entry.width, entry.height, entry.format) == (0, 0, "")
    assert (entry.date_taken, entry.indexed_at) == (0.0, 0.0)

    # And the migrated schema must accept new writes with the new columns.
    store.upsert(_entry(id="b1", path="/imgs/b.png", width=10, height=20), np.zeros(4, dtype=np.float32))
    store.save()
    assert store.get("b1").width == 10


def test_needs_reindex_detects_new_and_unchanged_files(tmp_path):
    img_path = tmp_path / "photo.png"
    img_path.write_bytes(b"fake-image-bytes")
    stat = img_path.stat()

    store = IndexStore(tmp_path / "idx", embedding_dim=4)
    store.load()
    assert store.needs_reindex(img_path) is True

    entry = _entry(
        path=str(img_path), mtime=stat.st_mtime, size=stat.st_size,
        width=10, height=10, format="PNG", date_taken=stat.st_mtime, indexed_at=1.0,
    )
    store.upsert(entry, np.zeros(4, dtype=np.float32))
    assert store.needs_reindex(img_path) is False


def test_needs_reindex_backfills_metadata_for_an_unchanged_legacy_row(tmp_path):
    img_path = tmp_path / "photo.png"
    img_path.write_bytes(b"fake-image-bytes")
    stat = img_path.stat()
    store = IndexStore(tmp_path / "idx", embedding_dim=4)
    store.load()
    store.upsert(
        _entry(path=str(img_path), mtime=stat.st_mtime, size=stat.st_size),
        np.zeros(4, dtype=np.float32),
    )

    assert store.needs_reindex(img_path) is True


def test_needs_reindex_true_when_file_changes(tmp_path):
    img_path = tmp_path / "photo.png"
    img_path.write_bytes(b"v1")
    stat = img_path.stat()
    store = IndexStore(tmp_path / "idx", embedding_dim=4)
    store.load()
    entry = _entry(path=str(img_path), mtime=stat.st_mtime, size=stat.st_size)
    store.upsert(entry, np.zeros(4, dtype=np.float32))

    img_path.write_bytes(b"v2-longer-content")
    assert store.needs_reindex(img_path) is True


def test_upsert_replaces_existing_entry_for_same_path(tmp_path):
    store = IndexStore(tmp_path, embedding_dim=4)
    store.load()
    store.upsert(_entry(path="/imgs/a.png"), np.array([1.0, 0, 0, 0], dtype=np.float32))
    store.upsert(_entry(path="/imgs/a.png"), np.array([0, 1.0, 0, 0], dtype=np.float32))
    assert len(store.all()) == 1
    assert store.get("a1") is not None
    assert store.get_embedding("a1").tolist() == [0, 1.0, 0, 0]


def test_prune_removes_missing_entries_and_keeps_embeddings_aligned(tmp_path):
    store = IndexStore(tmp_path, embedding_dim=4)
    store.load()
    store.upsert(_entry(id="a1", path="/imgs/a.png"), np.array([1, 0, 0, 0], dtype=np.float32))
    store.upsert(_entry(id="b1", path="/imgs/b.png"), np.array([0, 1, 0, 0], dtype=np.float32))
    store.upsert(_entry(id="c1", path="/imgs/c.png"), np.array([0, 0, 1, 0], dtype=np.float32))

    store.prune({"/imgs/a.png", "/imgs/c.png"})

    assert [e.id for e in store.all()] == ["a1", "c1"]
    assert store.embeddings.shape[0] == 2
    assert store.get_embedding("a1").tolist() == [1, 0, 0, 0]
    assert store.get_embedding("c1").tolist() == [0, 0, 1, 0]
    assert store.get("b1") is None
    assert store.get_embedding("b1") is None


def test_prune_to_empty_set_clears_entries_and_embeddings(tmp_path):
    store = IndexStore(tmp_path, embedding_dim=4)
    store.load()
    store.upsert(_entry(id="a1", path="/imgs/a.png"), np.array([1, 0, 0, 0], dtype=np.float32))

    store.prune(set())

    assert store.all() == []
    assert store.embeddings.shape == (0, 4)


def test_concurrent_upsert_keeps_entries_and_embeddings_aligned(tmp_path):
    store = IndexStore(tmp_path, embedding_dim=4)
    store.load()
    errors: list[Exception] = []

    def worker(n: int) -> None:
        try:
            for i in range(25):
                store.upsert(
                    _entry(id=f"t{n}-{i}", path=f"/imgs/t{n}-{i}.png"),
                    np.zeros(4, dtype=np.float32),
                )
        except Exception as exc:  # pragma: no cover - failure path
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert len(store.all()) == store.embeddings.shape[0] == 200


def test_corrupt_db_resets_to_empty(tmp_path):
    (tmp_path / "index.db").write_bytes(b"not a real sqlite database")

    store = IndexStore(tmp_path, embedding_dim=4)
    store.load()

    assert store.all() == []
    assert store.embeddings.shape == (0, 4)
    assert store.get("a1") is None
    assert list(tmp_path.glob("index.db.corrupt-*"))


def test_database_locked_error_is_not_misclassified_as_corruption(tmp_path, monkeypatch):
    db_path = tmp_path / "index.db"
    db_path.write_bytes(b"must remain untouched")

    def locked(_self):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(IndexStore, "_connect", locked)
    with pytest.raises(sqlite3.OperationalError, match="locked"):
        IndexStore(tmp_path, embedding_dim=4)

    assert db_path.read_bytes() == b"must remain untouched"
    assert not list(tmp_path.glob("index.db.corrupt-*"))


def test_migrates_legacy_json_and_npy_on_first_load(tmp_path):
    legacy_entry = {
        "id": "a1", "path": "/imgs/a.png", "thumbnail_path": "/thumbs/a1.jpg",
        "ocr_text": "NETBET", "colors": ["green"], "objects": ["clover"],
        "mtime": 123.0, "size": 456,
    }
    (tmp_path / "index.json").write_text(json.dumps([legacy_entry]))
    np.save(tmp_path / "embeddings.npy", np.array([[1.0, 0.0, 0.0, 0.0]], dtype=np.float32))

    store = IndexStore(tmp_path, embedding_dim=4)
    store.load()

    assert store.get("a1").ocr_text == "NETBET"
    assert store.get_embedding("a1").tolist() == [1.0, 0.0, 0.0, 0.0]
    assert store.get_by_path("/imgs/a.png").id == "a1"


def test_migration_skips_on_unreadable_json(tmp_path):
    (tmp_path / "index.json").write_bytes(b"not valid json")
    np.save(tmp_path / "embeddings.npy", np.array([[1.0, 0.0, 0.0, 0.0]], dtype=np.float32))

    store = IndexStore(tmp_path, embedding_dim=4)
    store.load()

    assert store.all() == []
    assert store.embeddings.shape == (0, 4)


def test_migration_skips_on_corrupted_embeddings_npy(tmp_path):
    legacy_entry = {
        "id": "a1", "path": "/imgs/a.png", "thumbnail_path": "/thumbs/a1.jpg",
        "ocr_text": "NETBET", "colors": ["green"], "objects": ["clover"],
        "mtime": 123.0, "size": 456,
    }
    (tmp_path / "index.json").write_text(json.dumps([legacy_entry]))
    (tmp_path / "embeddings.npy").write_bytes(b"corrupted npy data")

    store = IndexStore(tmp_path, embedding_dim=4)
    store.load()

    assert store.all() == []
    assert store.embeddings.shape == (0, 4)


def test_migration_skips_on_truncated_embeddings_npy(tmp_path):
    legacy_entry = {
        "id": "a1", "path": "/imgs/a.png", "thumbnail_path": "/thumbs/a1.jpg",
        "ocr_text": "NETBET", "colors": ["green"], "objects": ["clover"],
        "mtime": 123.0, "size": 456,
    }
    (tmp_path / "index.json").write_text(json.dumps([legacy_entry]))
    # Write a partial/truncated npy file (start of valid npy but incomplete)
    (tmp_path / "embeddings.npy").write_bytes(b"\x93NUMPY\x01\x00")

    store = IndexStore(tmp_path, embedding_dim=4)
    store.load()

    assert store.all() == []
    assert store.embeddings.shape == (0, 4)


def test_migration_skips_on_length_mismatch(tmp_path):
    legacy_entry = {
        "id": "a1", "path": "/imgs/a.png", "thumbnail_path": "/thumbs/a1.jpg",
        "ocr_text": "NETBET", "colors": ["green"], "objects": ["clover"],
        "mtime": 123.0, "size": 456,
    }
    (tmp_path / "index.json").write_text(json.dumps([legacy_entry]))
    # Save 2 embeddings but only 1 entry
    np.save(tmp_path / "embeddings.npy", np.array([[1.0, 0, 0, 0], [0, 1.0, 0, 0]], dtype=np.float32))

    store = IndexStore(tmp_path, embedding_dim=4)
    store.load()

    assert store.all() == []
    assert store.embeddings.shape == (0, 4)


def test_migration_skips_on_missing_required_field(tmp_path):
    # Entry missing 'size' field which is required
    legacy_entry = {
        "id": "a1", "path": "/imgs/a.png", "thumbnail_path": "/thumbs/a1.jpg",
        "ocr_text": "NETBET", "colors": ["green"], "objects": ["clover"],
        "mtime": 123.0,
        # 'size' is missing!
    }
    (tmp_path / "index.json").write_text(json.dumps([legacy_entry]))
    np.save(tmp_path / "embeddings.npy", np.array([[1.0, 0.0, 0.0, 0.0]], dtype=np.float32))

    store = IndexStore(tmp_path, embedding_dim=4)
    store.load()

    assert store.all() == []
    assert store.embeddings.shape == (0, 4)


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


def test_migration_marker_prevents_resurrecting_legitimately_pruned_entries(tmp_path):
    # Regression guard: the migration used to be gated on "table is empty",
    # which is also true after a legitimate prune(set()) — that would
    # re-import the never-deleted legacy index.json and resurrect rows that
    # were deliberately removed. A persistent one-shot marker (PRAGMA
    # user_version) must survive across IndexStore instances even though the
    # in-memory table is legitimately empty again.
    legacy_entry = {
        "id": "a1", "path": "/imgs/a.png", "thumbnail_path": "/thumbs/a1.jpg",
        "ocr_text": "NETBET", "colors": ["green"], "objects": ["clover"],
        "mtime": 123.0, "size": 456,
    }
    (tmp_path / "index.json").write_text(json.dumps([legacy_entry]))
    np.save(tmp_path / "embeddings.npy", np.array([[1.0, 0.0, 0.0, 0.0]], dtype=np.float32))

    store = IndexStore(tmp_path, embedding_dim=4)
    store.load()
    assert store.get("a1") is not None  # migration ran once, entry present

    store.prune(set())  # e.g. source folder emptied out
    store.save()
    assert store.all() == []

    # A fresh IndexStore over the same dir must NOT re-run the migration just
    # because the table happens to be empty again - the legacy index.json is
    # still on disk (left in place per spec) and would otherwise resurrect a1.
    reloaded = IndexStore(tmp_path, embedding_dim=4)
    reloaded.load()
    assert reloaded.all() == []


def test_corrupt_db_recovery_also_clears_stale_wal_sidecar(tmp_path):
    # A stale -wal sidecar surviving a fresh, empty main db file would get
    # replayed by SQLite into it, silently un-resetting the "fresh empty
    # index" the corruption-recovery path is supposed to produce.
    (tmp_path / "index.db").write_bytes(b"not a real sqlite database")
    (tmp_path / "index.db-wal").write_bytes(b"stale wal bytes that must not be replayed")
    (tmp_path / "index.db-shm").write_bytes(b"stale shm bytes")

    store = IndexStore(tmp_path, embedding_dim=4)
    store.load()

    assert store.all() == []
    assert store.embeddings.shape == (0, 4)
    assert store.get("a1") is None
    # SQLite may remove invalid sidecars itself while the first connection is
    # failing, or the replacement database may create fresh valid sidecars.
    # Either way, the stale bytes must not remain available for replay.
    wal = tmp_path / "index.db-wal"
    shm = tmp_path / "index.db-shm"
    assert not wal.exists() or wal.read_bytes() != b"stale wal bytes that must not be replayed"
    assert not shm.exists() or shm.read_bytes() != b"stale shm bytes"


def test_migration_logs_warning_on_duplicate_ids_but_keeps_last(tmp_path, caplog):
    legacy_entries = [
        {
            "id": "a1", "path": "/imgs/a.png", "thumbnail_path": "/thumbs/a1.jpg",
            "ocr_text": "first", "colors": [], "objects": [], "mtime": 1.0, "size": 1,
        },
        {
            "id": "a1", "path": "/imgs/a2.png", "thumbnail_path": "/thumbs/a1b.jpg",
            "ocr_text": "second", "colors": [], "objects": [], "mtime": 2.0, "size": 2,
        },
    ]
    (tmp_path / "index.json").write_text(json.dumps(legacy_entries))
    np.save(
        tmp_path / "embeddings.npy",
        np.array([[1.0, 0, 0, 0], [0, 1.0, 0, 0]], dtype=np.float32),
    )

    with caplog.at_level(logging.WARNING):
        store = IndexStore(tmp_path, embedding_dim=4)
        store.load()

    # Last one wins - one entry survives with the second entry's data - but
    # it must be observable that a row was silently dropped along the way.
    assert len(store.all()) == 1
    assert store.get("a1").ocr_text == "second"
    assert any("duplicate" in r.message.lower() for r in caplog.records)


def test_load_recovers_from_malformed_embedding_blob(tmp_path):
    store = IndexStore(tmp_path, embedding_dim=4)
    store.load()
    store.upsert(
        _entry(id="good1", path="/imgs/good.png"),
        np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32),
    )
    # Bypass upsert() to simulate row-level corruption surviving whatever
    # validation exists elsewhere (e.g. a partial/corrupt write at the SQLite
    # row level) - "short" is 5 bytes, not a multiple of float32's 4-byte
    # itemsize, so np.frombuffer raises ValueError on it.
    store._conn.execute(
        "INSERT INTO images "
        "(id, path, thumbnail_path, ocr_text, colors, objects, mtime, size, embedding) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("bad1", "/imgs/bad.png", "/thumbs/bad.jpg", "", "[]", "[]", 0.0, 0, b"short"),
    )
    store._conn.commit()

    fresh = IndexStore(tmp_path, embedding_dim=4)
    fresh.load()

    assert [entry.id for entry in fresh.all()] == ["good1"]
    assert fresh.embeddings.shape == (1, 4)


def test_failed_derived_upsert_rolls_back_the_primary_row(tmp_path, monkeypatch):
    store = IndexStore(tmp_path, embedding_dim=4)
    store.load()

    monkeypatch.setattr(store, "_sync_derived", lambda *args: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError, match="boom"):
        store.upsert(_entry(), np.zeros(4, dtype=np.float32))

    assert store.get("a1") is None
    assert store.count() == 0


def test_upsert_keeps_large_catalog_in_sqlite_without_an_in_memory_buffer(tmp_path):
    store = IndexStore(tmp_path, embedding_dim=4)
    store.load()
    n = 1500  # crosses the 1024-row initial-capacity boundary at least once
    for i in range(n):
        store.upsert(
            _entry(id=f"e{i}", path=f"/imgs/e{i}.png"),
            np.array([float(i), 0.0, 0.0, 1.0], dtype=np.float32),
        )

    assert len(store.entries) == n
    assert store.embeddings.shape == (n, 4)
    assert not hasattr(store, "_emb_buf")

    # Data integrity across a growth boundary: spot-check first, a
    # mid-growth, and the last entry.
    assert store.get_embedding("e0").tolist() == [0.0, 0.0, 0.0, 1.0]
    assert store.get_embedding("e1023").tolist() == [1023.0, 0.0, 0.0, 1.0]
    assert store.get_embedding(f"e{n - 1}").tolist() == [float(n - 1), 0.0, 0.0, 1.0]

    store.save()
    reloaded = IndexStore(tmp_path, embedding_dim=4)
    reloaded.load()
    assert reloaded.embeddings.shape == (n, 4)
    assert reloaded.get_embedding("e750").tolist() == [750.0, 0.0, 0.0, 1.0]
