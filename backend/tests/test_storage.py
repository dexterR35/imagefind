import numpy as np

from app.storage import ImageEntry, IndexStore


def _entry(path="/imgs/a.png", mtime=0.0, size=0):
    return ImageEntry(
        id="a1", path=path, thumbnail_path="/thumbs/a1.jpg",
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
