import numpy as np
import pytest
from PIL import Image

from app import config
from app import objects as objects_mod
from app.indexer import Indexer, ReindexJob
from app.storage import ImageEntry, IndexStore


@pytest.fixture(autouse=True)
def _skip_real_ram_load(monkeypatch):
    # These tests exercise the reindex loop's own logic (skipping, pruning,
    # cancellation, periodic saves, ...) via a fake process_image and have
    # nothing to do with RAM++ itself - stub out the eager readiness check
    # so they don't need a real multi-GB checkpoint on disk to pass.
    monkeypatch.setattr(objects_mod, "ensure_ram_ready", lambda: None)
    monkeypatch.setattr(objects_mod, "unload_ram_model", lambda: None)


def _make_images(images_dir, count=2):
    images_dir.mkdir(parents=True, exist_ok=True)
    for i in range(count):
        color = (200, 30, 30) if i % 2 == 0 else (30, 200, 30)
        Image.new("RGB", (64, 64), color).save(images_dir / f"img{i:03d}.png")


def _fake_process_image(index_dir):
    def process(path, settings):
        stat = path.stat()
        entry = ImageEntry(
            id=path.name, path=str(path),
            thumbnail_path=str(index_dir / "thumbnails" / f"{path.name}.jpg"),
            ocr_text="", objects=[], mtime=stat.st_mtime, size=stat.st_size,
            width=64, height=64, format="PNG", date_taken=stat.st_mtime, indexed_at=1.0,
        )
        return entry, np.zeros(512, dtype=np.float32)

    return process


def test_process_image_captures_dimensions_format_and_falls_back_date_taken(tmp_path):
    # Exercises the real pipeline (no monkeypatching of process_image itself)
    # to verify width/height/format/date_taken are actually populated from
    # the file - a synthetic PNG with no EXIF, so date_taken must fall back
    # to the file's mtime.
    images_dir = tmp_path / "images"
    images_dir.mkdir()
    img_path = images_dir / "photo.png"
    Image.new("RGB", (64, 48), (10, 20, 30)).save(img_path)
    expected_mtime = img_path.stat().st_mtime

    index_dir = tmp_path / "index"
    store = IndexStore(index_dir, embedding_dim=512)
    store.load()
    indexer = Indexer(images_dir, index_dir, store)

    entry, _ = indexer.process_image(img_path, indexer._current_settings())

    assert (entry.width, entry.height) == (64, 48)
    assert entry.format == "PNG"
    assert entry.date_taken == expected_mtime
    assert entry.indexed_at > 0


def test_process_image_removes_temporary_thumbnail_after_pipeline_failure(tmp_path, monkeypatch):
    images_dir = tmp_path / "images"
    _make_images(images_dir, count=1)
    index_dir = tmp_path / "index"
    store = IndexStore(index_dir, embedding_dim=512)
    store.load()
    indexer = Indexer(images_dir, index_dir, store)
    monkeypatch.setattr("app.indexer.embeddings.embed_image", lambda image: np.zeros(512, dtype=np.float32))
    monkeypatch.setattr(
        "app.indexer.ocr.extract_text",
        lambda path, *, image=None: (_ for _ in ()).throw(RuntimeError("OCR failed")),
    )

    with pytest.raises(RuntimeError, match="OCR failed"):
        indexer.process_image(images_dir / "img000.png", indexer._current_settings())

    assert list((index_dir / "thumbnails").glob("*")) == []


def test_cleanup_orphan_thumbnails_preserves_referenced_cache(tmp_path):
    images_dir = tmp_path / "images"
    images_dir.mkdir()
    index_dir = tmp_path / "index"
    thumbs = index_dir / "thumbnails"
    thumbs.mkdir(parents=True)
    referenced = thumbs / "keep.jpg"
    orphan = thumbs / "orphan.jpg"
    abandoned_temp = thumbs / "abandoned.jpg.tmp"
    unrelated = thumbs / "notes.txt"
    for path in (referenced, orphan, abandoned_temp, unrelated):
        path.write_bytes(b"cache")

    store = IndexStore(index_dir, embedding_dim=512)
    store.load()
    store.upsert(
        ImageEntry(
            id="keep", path=str(images_dir / "keep.png"), thumbnail_path=str(referenced),
            ocr_text="", objects=[], mtime=0.0, size=0,
        ),
        np.zeros(512, dtype=np.float32),
    )

    removed = Indexer(images_dir, index_dir, store).cleanup_orphan_thumbnails()

    assert removed == 2
    assert referenced.exists()
    assert unrelated.exists()
    assert not orphan.exists()
    assert not abandoned_temp.exists()


def test_run_reindex_processes_new_and_skips_unchanged(tmp_path, monkeypatch):
    images_dir = tmp_path / "images"
    _make_images(images_dir)

    index_dir = tmp_path / "index"
    store = IndexStore(index_dir, embedding_dim=512)
    store.load()
    indexer = Indexer(images_dir, index_dir, store)

    job = ReindexJob(id="job1")
    indexer.run_reindex(job)

    assert job.processed == 2
    assert job.total == 2
    assert job.failed == 0
    assert job.done is True
    assert len(store.all()) == 2

    calls = []
    original = indexer.process_image
    monkeypatch.setattr(indexer, "process_image", lambda p, s: (calls.append(p), original(p, s))[1])

    job2 = ReindexJob(id="job2")
    indexer.run_reindex(job2)

    assert job2.processed == 2
    assert len(store.all()) == 2
    assert calls == []


def test_run_reindex_unloads_ram_model_when_finished(tmp_path, monkeypatch):
    images_dir = tmp_path / "images"
    _make_images(images_dir, count=1)
    index_dir = tmp_path / "index"
    store = IndexStore(index_dir, embedding_dim=512)
    store.load()
    indexer = Indexer(images_dir, index_dir, store)
    monkeypatch.setattr(indexer, "process_image", _fake_process_image(index_dir))

    unload_calls = []
    monkeypatch.setattr(objects_mod, "unload_ram_model", lambda: unload_calls.append(True))

    job = ReindexJob(id="job1")
    indexer.run_reindex(job)

    assert job.done is True
    assert unload_calls == [True]


def test_run_reindex_skips_corrupt_image_without_aborting(tmp_path):
    images_dir = tmp_path / "images"
    _make_images(images_dir)
    (images_dir / "broken.png").write_bytes(b"not a real image")

    index_dir = tmp_path / "index"
    store = IndexStore(index_dir, embedding_dim=512)
    store.load()
    indexer = Indexer(images_dir, index_dir, store)

    job = ReindexJob(id="job1")
    indexer.run_reindex(job)

    assert job.processed == 3
    assert job.failed == 1
    assert job.done is True
    assert len(store.all()) == 2


def test_run_reindex_records_per_file_failures_with_paths(tmp_path):
    images_dir = tmp_path / "images"
    _make_images(images_dir)
    (images_dir / "broken.png").write_bytes(b"not a real image")

    index_dir = tmp_path / "index"
    store = IndexStore(index_dir, embedding_dim=512)
    store.load()
    indexer = Indexer(images_dir, index_dir, store)

    job = ReindexJob(id="job1")
    indexer.run_reindex(job)

    assert job.failed == 1
    assert len(job.failures) == 1
    assert job.failures[0]["path"].endswith("broken.png")
    assert job.failures[0]["error"]


def test_run_reindex_survives_unreadable_subdir_and_skips_prune(tmp_path):
    images_dir = tmp_path / "images"
    _make_images(images_dir, count=2)
    locked = images_dir / "locked"
    _make_images(locked, count=1)

    index_dir = tmp_path / "index"
    store = IndexStore(index_dir, embedding_dim=512)
    store.load()
    indexer = Indexer(images_dir, index_dir, store)

    import os as _os
    import stat as _stat

    _os.chmod(locked, 0)
    try:
        if _os.access(locked, _os.R_OK):  # running as root - the chmod is a no-op
            pytest.skip("cannot make a directory unreadable as root")

        job = ReindexJob(id="job1")
        indexer.run_reindex(job)
        assert job.error is None and job.done is True
        assert {e.path for e in store.all()} == {
            str(images_dir / "img000.png"), str(images_dir / "img001.png")
        }

        # A deleted top-level file must NOT be pruned while the folder scan is
        # still incomplete - a partial listing must never be read as "deleted".
        (images_dir / "img001.png").unlink()
        indexer.run_reindex(ReindexJob(id="job2"))
        assert store.get_by_path(str(images_dir / "img001.png")) is not None
    finally:
        _os.chmod(locked, _stat.S_IRWXU)


def test_run_reindex_prunes_entries_for_deleted_files(tmp_path):
    images_dir = tmp_path / "images"
    _make_images(images_dir)

    index_dir = tmp_path / "index"
    store = IndexStore(index_dir, embedding_dim=512)
    store.load()
    indexer = Indexer(images_dir, index_dir, store)

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


def test_reconciliation_confirms_missed_delete_twice_before_pruning(tmp_path, monkeypatch):
    images_dir = tmp_path / "images"
    _make_images(images_dir, count=1)
    index_dir = tmp_path / "index"
    store = IndexStore(index_dir, embedding_dim=512)
    store.load()
    indexer = Indexer(images_dir, index_dir, store)
    monkeypatch.setattr(indexer, "process_image", _fake_process_image(index_dir))

    indexer.run_reindex(ReindexJob(id="initial"))
    image_path = images_dir / "img000.png"
    image_path.unlink()

    indexer.run_reindex(ReindexJob(id="reconcile1"), confirm_deletions=True)
    assert store.get_by_path(str(image_path)) is not None

    indexer.run_reindex(ReindexJob(id="reconcile2"), confirm_deletions=True)
    assert store.get_by_path(str(image_path)) is None


def test_no_change_reconciliation_does_not_load_ram(tmp_path, monkeypatch):
    images_dir = tmp_path / "images"
    _make_images(images_dir, count=1)
    index_dir = tmp_path / "index"
    store = IndexStore(index_dir, embedding_dim=512)
    store.load()
    indexer = Indexer(images_dir, index_dir, store)
    monkeypatch.setattr(indexer, "process_image", _fake_process_image(index_dir))
    indexer.run_reindex(ReindexJob(id="initial"))

    calls = []
    monkeypatch.setattr(objects_mod, "ensure_ram_ready", lambda: calls.append(True))
    indexer.run_reindex(ReindexJob(id="reconcile"), confirm_deletions=True)

    assert calls == []


def test_run_reindex_aborts_without_pruning_when_images_dir_is_unreachable(tmp_path, monkeypatch):
    # Regression guard: images_dir living on a network mount (e.g. a NAS share)
    # can disappear out from under the app (unmounted, dropped, not yet mounted
    # at startup). Path.rglob() on a missing directory silently returns []
    # rather than raising, which used to look identical to "the folder is
    # genuinely empty" - and prune() then wiped every existing entry.
    images_dir = tmp_path / "images"
    _make_images(images_dir)

    index_dir = tmp_path / "index"
    store = IndexStore(index_dir, embedding_dim=512)
    store.load()
    indexer = Indexer(images_dir, index_dir, store)
    monkeypatch.setattr(indexer, "process_image", _fake_process_image(index_dir))

    job = ReindexJob(id="job1")
    indexer.run_reindex(job)
    assert len(store.all()) == 2

    import shutil
    shutil.rmtree(images_dir)  # simulate the NAS mount disappearing

    job2 = ReindexJob(id="job2")
    indexer.run_reindex(job2)

    assert len(store.all()) == 2, "existing entries must survive an unreachable images_dir"
    assert job2.error is not None
    assert job2.done is True


def test_run_reindex_prunes_files_deleted_during_the_scan(tmp_path, monkeypatch):
    images_dir = tmp_path / "images"
    _make_images(images_dir, count=3)

    index_dir = tmp_path / "index"
    store = IndexStore(index_dir, embedding_dim=512)
    store.load()
    indexer = Indexer(images_dir, index_dir, store)

    fake_process = _fake_process_image(index_dir)
    monkeypatch.setattr(indexer, "process_image", fake_process)

    job = ReindexJob(id="job1")
    indexer.run_reindex(job)
    assert len(store.all()) == 3

    # img000 is changed so it gets reprocessed; while it's being reprocessed,
    # img002 (unchanged, so it's never reprocessed itself) is deleted from
    # disk — simulating a file vanishing mid-scan, after run_reindex's
    # initial directory listing but before its own turn in the loop.
    (images_dir / "img000.png").write_bytes(b"changed bytes to force reprocessing")

    def process_and_delete_another(path, settings):
        (images_dir / "img002.png").unlink(missing_ok=True)
        return fake_process(path, settings)

    monkeypatch.setattr(indexer, "process_image", process_and_delete_another)

    job2 = ReindexJob(id="job2")
    indexer.run_reindex(job2)

    remaining_paths = {e.path for e in store.all()}
    assert str(images_dir / "img002.png") not in remaining_paths
    assert len(store.all()) == 2
    # needs_reindex(img002) hits a missing file mid-loop (it was already
    # deleted) — caught per-image rather than aborting the batch.
    assert job2.failed == 1


def test_run_reindex_force_reprocesses_unchanged_files(tmp_path, monkeypatch):
    images_dir = tmp_path / "images"
    _make_images(images_dir)

    index_dir = tmp_path / "index"
    store = IndexStore(index_dir, embedding_dim=512)
    store.load()
    indexer = Indexer(images_dir, index_dir, store)

    calls = []
    fake_process = _fake_process_image(index_dir)

    def counting_process(path, settings):
        calls.append(path)
        return fake_process(path, settings)

    monkeypatch.setattr(indexer, "process_image", counting_process)

    job = ReindexJob(id="job1")
    indexer.run_reindex(job)
    assert len(calls) == 2
    assert len(store.all()) == 2

    job2 = ReindexJob(id="job2")
    indexer.run_reindex(job2)
    assert len(calls) == 2, "unchanged files should be skipped without force"

    job3 = ReindexJob(id="job3")
    indexer.run_reindex(job3, force=True)
    assert len(calls) == 4, "force=True should reprocess unchanged files too"
    assert len(store.all()) == 2


def test_run_reindex_saves_periodically_not_just_at_the_end(tmp_path, monkeypatch):
    images_dir = tmp_path / "images"
    _make_images(images_dir, count=125)

    index_dir = tmp_path / "index"
    store = IndexStore(index_dir, embedding_dim=512)
    store.load()
    indexer = Indexer(images_dir, index_dir, store)

    monkeypatch.setattr(indexer, "process_image", _fake_process_image(index_dir))

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


def test_run_reindex_snapshots_settings_once_at_start(tmp_path, monkeypatch):
    images_dir = tmp_path / "images"
    _make_images(images_dir, count=1)

    index_dir = tmp_path / "index"
    store = IndexStore(index_dir, embedding_dim=512)
    store.load()
    indexer = Indexer(images_dir, index_dir, store)

    original_confidence = config.RAM_CONFIDENCE
    fake_process = _fake_process_image(index_dir)
    captured = []

    def process_and_mutate_config(path, settings):
        captured.append(settings)
        # A settings change "arriving" mid-run must not affect a snapshot
        # already taken at the start of run_reindex.
        monkeypatch.setattr(config, "RAM_CONFIDENCE", 0.999)
        return fake_process(path, settings)

    monkeypatch.setattr(indexer, "process_image", process_and_mutate_config)

    job = ReindexJob(id="job1")
    indexer.run_reindex(job)

    assert len(captured) == 1
    assert captured[0].ram_confidence == original_confidence


def test_run_reindex_snapshots_custom_tags_once_at_start(tmp_path, monkeypatch):
    images_dir = tmp_path / "images"
    _make_images(images_dir, count=1)

    index_dir = tmp_path / "index"
    store = IndexStore(index_dir, embedding_dim=512)
    store.load()
    indexer = Indexer(images_dir, index_dir, store, custom_tags=["zeus"])

    fake_process = _fake_process_image(index_dir)
    captured = []

    def process_and_mutate(path, settings):
        captured.append(settings)
        indexer.custom_tags = ["changed mid-run"]
        return fake_process(path, settings)

    monkeypatch.setattr(indexer, "process_image", process_and_mutate)

    job = ReindexJob(id="job1")
    indexer.run_reindex(job)

    assert len(captured) == 1
    assert captured[0].custom_tags == ["zeus"]


def test_run_reindex_clears_custom_tag_embedding_cache_at_start(tmp_path, monkeypatch):
    images_dir = tmp_path / "images"
    _make_images(images_dir, count=1)

    index_dir = tmp_path / "index"
    store = IndexStore(index_dir, embedding_dim=512)
    store.load()
    indexer = Indexer(images_dir, index_dir, store)

    monkeypatch.setattr(indexer, "process_image", _fake_process_image(index_dir))
    objects_mod._tag_embedding_cache["zeus"] = "stale-cached-value"

    job = ReindexJob(id="job1")
    indexer.run_reindex(job)

    # Otherwise adding/changing reference images for an existing custom tag
    # and reindexing wouldn't actually pick them up until a full restart.
    assert "zeus" not in objects_mod._tag_embedding_cache


def test_run_reindex_fails_fast_without_processing_any_images_when_ram_not_ready(tmp_path, monkeypatch):
    images_dir = tmp_path / "images"
    _make_images(images_dir, count=3)

    index_dir = tmp_path / "index"
    store = IndexStore(index_dir, embedding_dim=512)
    store.load()
    indexer = Indexer(images_dir, index_dir, store)

    def _boom():
        raise FileNotFoundError("RAM++ checkpoint not found at '...'")

    monkeypatch.setattr(objects_mod, "ensure_ram_ready", _boom)
    unload_calls = []
    monkeypatch.setattr(objects_mod, "unload_ram_model", lambda: unload_calls.append(True))
    calls = []
    monkeypatch.setattr(indexer, "process_image", lambda p, s: calls.append(p))

    job = ReindexJob(id="job1")
    indexer.run_reindex(job)

    assert calls == [], "no image should be attempted once the RAM++ readiness check fails"
    assert job.processed == 0
    assert job.failed == 0
    assert job.done is True
    assert job.error is not None and "checkpoint" in job.error.lower()
    assert len(store.all()) == 0
    assert unload_calls == [True]


def test_run_reindex_stops_when_cancelled_and_keeps_partial_progress(tmp_path, monkeypatch):
    images_dir = tmp_path / "images"
    _make_images(images_dir, count=3)

    index_dir = tmp_path / "index"
    store = IndexStore(index_dir, embedding_dim=512)
    store.load()
    indexer = Indexer(images_dir, index_dir, store)

    job = ReindexJob(id="job1")
    fake_process = _fake_process_image(index_dir)

    def process_then_cancel(path, settings):
        # Simulate a stop request arriving while the first image is
        # in-flight - the loop should notice it before starting the next one.
        result = fake_process(path, settings)
        job.cancel_event.set()
        return result

    monkeypatch.setattr(indexer, "process_image", process_then_cancel)
    unload_calls = []
    monkeypatch.setattr(objects_mod, "unload_ram_model", lambda: unload_calls.append(True))

    indexer.run_reindex(job)

    assert job.cancelled is True
    assert job.done is True
    assert job.error is None
    assert job.processed == 1
    assert len(store.all()) == 1, "the one image already processed before cancelling must stay indexed"
    assert unload_calls == [True]
