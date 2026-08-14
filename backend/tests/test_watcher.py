import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from PIL import Image

from app.indexer import Indexer
from app.storage import IndexStore
from app.watcher import _Handler, _wait_until_stable


def _fake_event(path, is_directory=False):
    return SimpleNamespace(src_path=str(path), is_directory=is_directory)


def test_wait_until_stable_returns_true_once_size_stops_changing(tmp_path):
    path = tmp_path / "photo.png"
    path.write_bytes(b"x" * 100)
    assert _wait_until_stable(path, checks=2, interval=0.01) is True


def test_wait_until_stable_returns_false_for_missing_file(tmp_path):
    assert _wait_until_stable(tmp_path / "missing.png", checks=2, interval=0.01) is False


def test_wait_until_stable_returns_false_when_size_keeps_changing(tmp_path):
    path = tmp_path / "still-copying.png"
    path.write_bytes(b"x")
    sizes = [10, 20, 30, 40]
    size_iter = iter(sizes)
    # Patch Path.stat at the class level for this test
    with patch.object(Path, "stat", lambda self: SimpleNamespace(st_size=next(size_iter))):
        assert _wait_until_stable(path, checks=3, interval=0.01) is False


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
        )
        return entry, np.zeros(512, dtype=np.float32)

    monkeypatch.setattr(indexer, "process_image", fake_process_image)
    monkeypatch.setattr("app.watcher._STABLE_CHECK_INTERVAL", 0.01)

    handler = _Handler(indexer, store)
    handler.on_modified(_fake_event(img_path))

    assert store.get_by_path(str(img_path)) is not None
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

    handler = _Handler(indexer, store)
    handler.on_modified(_fake_event(images_dir / "notes.txt"))
    handler.on_modified(_fake_event(images_dir / "subfolder", is_directory=True))

    assert calls == []
