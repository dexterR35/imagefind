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
