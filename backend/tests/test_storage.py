import json
import logging
import threading

import numpy as np

from app.storage import ImageEntry, IndexStore


def _entry(path="/imgs/a.png", mtime=0.0, size=0, id="a1"):
    return ImageEntry(
        id=id, path=path, thumbnail_path=f"/thumbs/{id}.jpg",
        ocr_text="NETBET", colors=["green"], objects=["clover"],
        mtime=mtime, size=size,
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


def test_needs_reindex_detects_new_and_unchanged_files(tmp_path):
    img_path = tmp_path / "photo.png"
    img_path.write_bytes(b"fake-image-bytes")
    stat = img_path.stat()

    store = IndexStore(tmp_path / "idx", embedding_dim=4)
    store.load()
    assert store.needs_reindex(img_path) is True

    entry = _entry(path=str(img_path), mtime=stat.st_mtime, size=stat.st_size)
    store.upsert(entry, np.zeros(4, dtype=np.float32))
    assert store.needs_reindex(img_path) is False


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
    fresh.load()  # must not raise, per spec's "reset to empty rather than crash" policy

    assert fresh.all() == []
    assert fresh.embeddings.shape == (0, 4)
