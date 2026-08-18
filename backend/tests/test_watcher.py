import threading
import time
from types import SimpleNamespace

import numpy as np
from PIL import Image

from app.indexer import Indexer, ReindexJob
from app.storage import IndexStore
from app.watcher import _Handler, _wait_until_stable, start_reconciliation_loop


def _fake_event(path, is_directory=False, destination=None):
    values = {"src_path": str(path), "is_directory": is_directory}
    if destination is not None:
        values["dest_path"] = str(destination)
    return SimpleNamespace(**values)


def test_wait_until_stable_returns_true_once_size_stops_changing(tmp_path):
    path = tmp_path / "photo.png"
    path.write_bytes(b"x" * 100)
    assert _wait_until_stable(path, checks=2, interval=0.01) is True


def test_wait_until_stable_returns_false_for_missing_file(tmp_path):
    assert _wait_until_stable(tmp_path / "missing.png", checks=2, interval=0.01) is False


def test_wait_until_stable_returns_false_when_size_keeps_changing(tmp_path, monkeypatch):
    path = tmp_path / "still-copying.png"
    path.write_bytes(b"x")
    sizes = [10, 20, 30, 40]
    size_iter = iter(sizes)
    # Instance-scoped patch: pathlib.Path uses __slots__, so a plain
    # monkeypatch.setattr(path, "stat", ...) can't set an instance
    # attribute directly (AttributeError: attribute is read-only). Patching
    # a one-off subclass instead of the whole Path class keeps the mock
    # scoped to just this one path object, so it can't be silently consumed
    # by an unrelated Path.stat() call elsewhere in the same test.
    class _OneOffPath(type(path)):
        pass

    scoped_path = _OneOffPath(path)
    monkeypatch.setattr(_OneOffPath, "stat", lambda self: SimpleNamespace(st_size=next(size_iter)))
    assert _wait_until_stable(scoped_path, checks=3, interval=0.01) is False


def test_wait_until_stable_detects_same_size_content_changes(tmp_path, monkeypatch):
    path = tmp_path / "still-changing.png"
    path.write_bytes(b"xxxx")
    mtimes = iter([1, 2, 3])

    class _OneOffPath(type(path)):
        pass

    scoped_path = _OneOffPath(path)
    monkeypatch.setattr(
        _OneOffPath,
        "stat",
        lambda self: SimpleNamespace(st_size=4, st_mtime_ns=next(mtimes)),
    )
    assert _wait_until_stable(scoped_path, checks=3, interval=0.01) is False


def test_handler_on_modified_processes_and_upserts_image(tmp_path, monkeypatch):
    images_dir = tmp_path / "images"
    images_dir.mkdir()
    index_dir = tmp_path / "index"
    img_path = images_dir / "new.png"
    Image.new("RGB", (32, 32), (10, 20, 30)).save(img_path)

    store = IndexStore(index_dir, embedding_dim=512)
    store.load()
    indexer = Indexer(images_dir, index_dir, store)

    def fake_process_image(path, settings):
        stat = path.stat()
        from app.storage import ImageEntry
        entry = ImageEntry(
            id="new1", path=str(path), thumbnail_path=str(index_dir / "t.jpg"),
            ocr_text="", colors=[], objects=[], mtime=stat.st_mtime, size=stat.st_size,
            width=32, height=32, format="PNG", date_taken=stat.st_mtime, indexed_at=1.0,
        )
        return entry, np.zeros(512, dtype=np.float32)

    monkeypatch.setattr(indexer, "process_image", fake_process_image)
    monkeypatch.setattr("app.watcher._STABLE_CHECK_INTERVAL", 0.01)

    handler = _Handler(indexer)
    handler.on_modified(_fake_event(img_path))

    assert store.get_by_path(str(img_path)) is not None
    assert store.get_by_path(str(img_path)).id == "new1"


def test_handler_on_modified_skips_reprocessing_an_already_indexed_unchanged_file(tmp_path, monkeypatch):
    # A single file copy onto a watched folder typically fires several
    # watchdog events (create + multiple modifies). Once the file has been
    # processed and its (mtime, size) match what's on disk, a later event for
    # the same unchanged file must not trigger a second full inference pass.
    images_dir = tmp_path / "images"
    images_dir.mkdir()
    index_dir = tmp_path / "index"
    img_path = images_dir / "new.png"
    Image.new("RGB", (32, 32), (10, 20, 30)).save(img_path)

    store = IndexStore(index_dir, embedding_dim=512)
    store.load()
    indexer = Indexer(images_dir, index_dir, store)

    calls = []

    def fake_process_image(path, settings):
        calls.append(path)
        stat = path.stat()
        from app.storage import ImageEntry
        entry = ImageEntry(
            id="new1", path=str(path), thumbnail_path=str(index_dir / "t.jpg"),
            ocr_text="", colors=[], objects=[], mtime=stat.st_mtime, size=stat.st_size,
            width=32, height=32, format="PNG", date_taken=stat.st_mtime, indexed_at=1.0,
        )
        return entry, np.zeros(512, dtype=np.float32)

    monkeypatch.setattr(indexer, "process_image", fake_process_image)
    monkeypatch.setattr("app.watcher._STABLE_CHECK_INTERVAL", 0.01)

    handler = _Handler(indexer)
    handler.on_modified(_fake_event(img_path))
    handler.on_modified(_fake_event(img_path))

    assert len(calls) == 1
    assert store.get_by_path(str(img_path)).id == "new1"


def test_handler_ignores_non_image_and_directory_events(tmp_path, monkeypatch):
    images_dir = tmp_path / "images"
    images_dir.mkdir()
    index_dir = tmp_path / "index"
    store = IndexStore(index_dir, embedding_dim=512)
    store.load()
    indexer = Indexer(images_dir, index_dir, store)

    calls = []
    monkeypatch.setattr(indexer, "process_image", lambda path, settings: calls.append(path))

    handler = _Handler(indexer)
    handler.on_modified(_fake_event(images_dir / "notes.txt"))
    handler.on_modified(_fake_event(images_dir / "subfolder", is_directory=True))

    assert calls == []


def test_handler_on_deleted_removes_entry(tmp_path):
    images_dir = tmp_path / "images"
    images_dir.mkdir()
    index_dir = tmp_path / "index"
    img_path = images_dir / "gone.png"

    store = IndexStore(index_dir, embedding_dim=512)
    store.load()
    from app.storage import ImageEntry
    store.upsert(
        ImageEntry(
            id="gone1", path=str(img_path), thumbnail_path=str(index_dir / "t.jpg"),
            ocr_text="", colors=[], objects=[], mtime=0.0, size=0,
        ),
        np.zeros(512, dtype=np.float32),
    )
    indexer = Indexer(images_dir, index_dir, store)

    handler = _Handler(indexer)
    handler.on_deleted(_fake_event(img_path))

    assert store.get_by_path(str(img_path)) is None


def test_handler_on_moved_removes_old_path_and_indexes_destination(tmp_path, monkeypatch):
    images_dir = tmp_path / "images"
    images_dir.mkdir()
    index_dir = tmp_path / "index"
    old_path = images_dir / "old.png"
    new_path = images_dir / "Promo ™ – new.png"
    Image.new("RGB", (32, 32), "white").save(new_path)

    store = IndexStore(index_dir, embedding_dim=512)
    store.load()
    from app.storage import ImageEntry
    store.upsert(
        ImageEntry(
            id="old1", path=str(old_path), thumbnail_path=str(index_dir / "old.jpg"),
            ocr_text="", colors=[], objects=[], mtime=0.0, size=0,
        ),
        np.zeros(512, dtype=np.float32),
    )
    indexer = Indexer(images_dir, index_dir, store)

    def fake_index(path, settings=None, force=False):
        stat = path.stat()
        store.upsert(
            ImageEntry(
                id="new1", path=str(path), thumbnail_path=str(index_dir / "new.jpg"),
                ocr_text="", colors=[], objects=[], mtime=stat.st_mtime, size=stat.st_size,
            ),
            np.zeros(512, dtype=np.float32),
        )
        return True

    monkeypatch.setattr(indexer, "index_path_if_needed", fake_index)
    monkeypatch.setattr("app.watcher._STABLE_CHECK_INTERVAL", 0.01)

    _Handler(indexer).on_moved(_fake_event(old_path, destination=new_path))

    assert store.get_by_path(str(old_path)) is None
    assert store.get_by_path(str(new_path)).id == "new1"


def test_handler_on_deleted_directory_removes_children_and_thumbnails(tmp_path):
    images_dir = tmp_path / "images"
    images_dir.mkdir()
    deleted_dir = images_dir / "deleted-campaign"
    index_dir = tmp_path / "index"
    thumbnails_dir = index_dir / "thumbnails"
    thumbnails_dir.mkdir(parents=True)
    store = IndexStore(index_dir, embedding_dim=512)
    store.load()
    from app.storage import ImageEntry

    thumbnails = []
    for number in range(2):
        thumbnail = thumbnails_dir / f"gone{number}.jpg"
        thumbnail.write_bytes(b"thumbnail")
        thumbnails.append(thumbnail)
        store.upsert(
            ImageEntry(
                id=f"gone{number}", path=str(deleted_dir / f"image{number}.png"),
                thumbnail_path=str(thumbnail), ocr_text="", colors=[], objects=[],
                mtime=0.0, size=0,
            ),
            np.zeros(512, dtype=np.float32),
        )

    indexer = Indexer(images_dir, index_dir, store)
    _Handler(indexer).on_deleted(_fake_event(deleted_dir, is_directory=True))

    assert store.count() == 0
    assert all(not thumbnail.exists() for thumbnail in thumbnails)


def test_handler_does_not_delete_index_when_nas_root_is_unreachable(tmp_path):
    images_dir = tmp_path / "images"
    images_dir.mkdir()
    index_dir = tmp_path / "index"
    image_path = images_dir / "keep.png"
    store = IndexStore(index_dir, embedding_dim=512)
    store.load()
    from app.storage import ImageEntry
    store.upsert(
        ImageEntry(
            id="keep1", path=str(image_path), thumbnail_path=str(index_dir / "keep.jpg"),
            ocr_text="", colors=[], objects=[], mtime=0.0, size=0,
        ),
        np.zeros(512, dtype=np.float32),
    )
    images_dir.rmdir()

    _Handler(Indexer(images_dir, index_dir, store)).on_deleted(_fake_event(image_path))

    assert store.get_by_path(str(image_path)) is not None


def test_reconciliation_loop_calls_run_reindex_on_interval(tmp_path, monkeypatch):
    images_dir = tmp_path / "images"
    images_dir.mkdir()
    index_dir = tmp_path / "index"
    store = IndexStore(index_dir, embedding_dim=512)
    store.load()
    indexer = Indexer(images_dir, index_dir, store)

    calls = []
    monkeypatch.setattr(indexer, "run_reindex", lambda job, **kwargs: calls.append((job, kwargs)))

    stop_event = threading.Event()
    thread = start_reconciliation_loop(
        indexer, lambda: ReindexJob(id="r1"), interval_seconds=0.01, stop_event=stop_event
    )
    time.sleep(0.05)
    stop_event.set()
    thread.join(timeout=2)

    assert len(calls) >= 1
    assert all(kwargs == {"confirm_deletions": True} for _, kwargs in calls)
    assert not thread.is_alive()
