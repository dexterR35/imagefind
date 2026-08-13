import numpy as np
from PIL import Image

from app.indexer import Indexer, ReindexJob
from app.storage import ImageEntry, IndexStore


def _make_images(images_dir, count=2):
    images_dir.mkdir(parents=True, exist_ok=True)
    for i in range(count):
        color = (200, 30, 30) if i % 2 == 0 else (30, 200, 30)
        Image.new("RGB", (64, 64), color).save(images_dir / f"img{i:03d}.png")


def test_run_reindex_processes_new_and_skips_unchanged(tmp_path, monkeypatch):
    images_dir = tmp_path / "images"
    _make_images(images_dir)

    index_dir = tmp_path / "index"
    store = IndexStore(index_dir, embedding_dim=512)
    store.load()
    indexer = Indexer(images_dir, index_dir, store, vocabulary=["clover"])

    job = ReindexJob(id="job1")
    indexer.run_reindex(job)

    assert job.processed == 2
    assert job.total == 2
    assert job.done is True
    assert len(store.all()) == 2

    calls = []
    original = indexer.process_image
    monkeypatch.setattr(indexer, "process_image", lambda p: (calls.append(p), original(p))[1])

    job2 = ReindexJob(id="job2")
    indexer.run_reindex(job2)

    assert job2.processed == 2
    assert len(store.all()) == 2
    assert calls == []


def test_run_reindex_skips_corrupt_image_without_aborting(tmp_path):
    images_dir = tmp_path / "images"
    _make_images(images_dir)
    (images_dir / "broken.png").write_bytes(b"not a real image")

    index_dir = tmp_path / "index"
    store = IndexStore(index_dir, embedding_dim=512)
    store.load()
    indexer = Indexer(images_dir, index_dir, store, vocabulary=["clover"])

    job = ReindexJob(id="job1")
    indexer.run_reindex(job)

    assert job.processed == 3
    assert job.done is True
    assert len(store.all()) == 2


def test_run_reindex_prunes_entries_for_deleted_files(tmp_path):
    images_dir = tmp_path / "images"
    _make_images(images_dir)

    index_dir = tmp_path / "index"
    store = IndexStore(index_dir, embedding_dim=512)
    store.load()
    indexer = Indexer(images_dir, index_dir, store, vocabulary=["clover"])

    job = ReindexJob(id="job1")
    indexer.run_reindex(job)
    assert len(store.all()) == 2

    deleted_path = str(images_dir / "img001.png")
    (images_dir / "img001.png").unlink()

    job2 = ReindexJob(id="job2")
    indexer.run_reindex(job2)

    remaining_paths = {e.path for e in store.all()}
    assert deleted_path not in remaining_paths
    assert len(store.all()) == 1
    assert store.embeddings.shape[0] == 1


def test_run_reindex_saves_periodically_not_just_at_the_end(tmp_path, monkeypatch):
    images_dir = tmp_path / "images"
    _make_images(images_dir, count=125)

    index_dir = tmp_path / "index"
    store = IndexStore(index_dir, embedding_dim=512)
    store.load()
    indexer = Indexer(images_dir, index_dir, store, vocabulary=["clover"])

    def fake_process_image(path):
        stat = path.stat()
        entry = ImageEntry(
            id=path.name, path=str(path),
            thumbnail_path=str(index_dir / "thumbnails" / f"{path.name}.jpg"),
            ocr_text="", colors=[], objects=[], mtime=stat.st_mtime, size=stat.st_size,
        )
        return entry, np.zeros(512, dtype=np.float32)

    monkeypatch.setattr(indexer, "process_image", fake_process_image)

    save_call_count = 0
    original_save = store.save

    def counting_save():
        nonlocal save_call_count
        save_call_count += 1
        original_save()

    monkeypatch.setattr(store, "save", counting_save)

    job = ReindexJob(id="job1")
    indexer.run_reindex(job)

    assert job.processed == 125
    assert job.done is True
    # 125 images with a save every 50 processed (at 50, 100) plus the final save
    # after the loop == at least 3 saves, not just the one at the very end.
    assert save_call_count >= 3
