import logging
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from . import config
from .indexer import IMAGE_EXTENSIONS, Indexer
from .storage import IndexStore

if TYPE_CHECKING:
    from .indexer import ReindexJob

logger = logging.getLogger(__name__)

_STABLE_CHECK_INTERVAL = config.WATCHER_STABLE_CHECK_SECONDS


def _wait_until_stable(path: Path, checks: int = 3, interval: float = _STABLE_CHECK_INTERVAL) -> bool:
    """False means "still changing" (or missing) after `checks` attempts —
    the caller should skip this event rather than read a partial file. A
    later event (or the reconciliation backstop) will catch it once the
    write actually finishes."""
    last_size = -1
    for _ in range(checks):
        try:
            size = path.stat().st_size
        except OSError:
            return False
        if size == last_size:
            return True
        last_size = size
        time.sleep(interval)
    return False


class _Handler(FileSystemEventHandler):
    def __init__(self, indexer: Indexer, store: IndexStore):
        self._indexer = indexer
        self._store = store

    def on_created(self, event):
        self._handle_changed(event)

    def on_modified(self, event):
        self._handle_changed(event)

    def on_deleted(self, event):
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.suffix.lower() not in IMAGE_EXTENSIONS:
            return
        self._store.delete_by_path(str(path))
        self._store.save()

    def _handle_changed(self, event):
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.suffix.lower() not in IMAGE_EXTENSIONS:
            return
        if not _wait_until_stable(path, interval=_STABLE_CHECK_INTERVAL):
            return
        try:
            if not self._store.needs_reindex(path):
                return
        except OSError:
            return
        settings = self._indexer._current_settings()
        try:
            entry, embedding = self._indexer.process_image(path, settings)
        except Exception:
            logger.warning("watcher: failed to process %s", path, exc_info=True)
            return
        self._store.upsert(entry, embedding)
        self._store.save()


def start_watcher(indexer: Indexer, store: IndexStore) -> Observer:
    observer = Observer()
    observer.schedule(_Handler(indexer, store), str(indexer.images_dir), recursive=True)
    observer.start()
    return observer


def start_reconciliation_loop(
    indexer: Indexer,
    job_factory: Callable[[], "ReindexJob"],
    interval_seconds: float,
    stop_event: threading.Event,
) -> threading.Thread:
    def loop():
        while not stop_event.wait(interval_seconds):
            job = job_factory()
            try:
                indexer.run_reindex(job)
            except Exception:
                logger.warning("reconciliation: run_reindex failed", exc_info=True)

    thread = threading.Thread(target=loop, daemon=True)
    thread.start()
    return thread
