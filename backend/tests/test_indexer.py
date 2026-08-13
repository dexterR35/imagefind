from PIL import Image

from app.indexer import Indexer, ReindexJob
from app.storage import IndexStore


def _make_images(images_dir):
    images_dir.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 64), (200, 30, 30)).save(images_dir / "a.png")
    Image.new("RGB", (64, 64), (30, 200, 30)).save(images_dir / "b.png")


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
