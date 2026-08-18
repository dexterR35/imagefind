import logging
import queue
import threading
import time
from _thread import LockType
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Literal

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from . import config
from .indexer import IMAGE_EXTENSIONS, Indexer

if TYPE_CHECKING:
    from .indexer import ReindexJob

logger = logging.getLogger(__name__)

_STABLE_CHECK_INTERVAL = config.WATCHER_STABLE_CHECK_SECONDS
_WorkKind = Literal["changed", "deleted", "scan_directory", "deleted_directory"]


def _wait_until_stable(path: Path, checks: int = 3, interval: float = _STABLE_CHECK_INTERVAL) -> bool:
    """Return true once a copied file's size and mtime have stopped changing."""
    last_signature: tuple[int, int] | None = None
    for _ in range(checks):
        try:
            stat = path.stat()
            signature = (stat.st_size, getattr(stat, "st_mtime_ns", 0))
        except OSError:
            return False
        if signature == last_signature:
            return True
        last_signature = signature
        time.sleep(interval)
    return False


class _Handler(FileSystemEventHandler):
    def __init__(
        self,
        indexer: Indexer,
        submit: Callable[[_WorkKind, Path], None] | None = None,
    ):
        self._indexer = indexer
        # Tests and direct callers remain synchronous; production passes the
        # queue's submit method so watchdog's OS event thread is never blocked
        # by OCR/RAM++ inference.
        self._submit_callback = submit

    def _submit(self, kind: _WorkKind, path: Path) -> None:
        if self._submit_callback is None:
            self.process(kind, path)
        else:
            self._submit_callback(kind, path)

    def on_created(self, event):
        path = Path(event.src_path)
        if event.is_directory:
            self._submit("scan_directory", path)
        elif path.suffix.lower() in IMAGE_EXTENSIONS:
            self._submit("changed", path)

    def on_modified(self, event):
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.suffix.lower() in IMAGE_EXTENSIONS:
            self._submit("changed", path)

    def on_deleted(self, event):
        path = Path(event.src_path)
        if event.is_directory:
            self._submit("deleted_directory", path)
        elif path.suffix.lower() in IMAGE_EXTENSIONS:
            self._submit("deleted", path)

    def on_moved(self, event):
        source = Path(event.src_path)
        destination = Path(event.dest_path)
        if event.is_directory:
            self._submit("deleted_directory", source)
            self._submit("scan_directory", destination)
            return
        if source.suffix.lower() in IMAGE_EXTENSIONS:
            self._submit("deleted", source)
        if destination.suffix.lower() in IMAGE_EXTENSIONS:
            self._submit("changed", destination)

    def process(self, kind: _WorkKind, path: Path) -> None:
        if kind == "changed":
            self._process_changed(path)
        elif kind == "deleted":
            self._process_deleted(path)
        elif kind == "scan_directory":
            self._process_directory(path)
        elif kind == "deleted_directory":
            self._process_deleted_directory(path)

    def _process_changed(self, path: Path) -> None:
        if path.suffix.lower() not in IMAGE_EXTENSIONS:
            return
        if not _wait_until_stable(path, interval=_STABLE_CHECK_INTERVAL):
            return
        try:
            self._indexer.index_path_if_needed(path)
        except (FileNotFoundError, OSError):
            # A newer rename/delete event will handle a file that disappeared
            # while this older create/modify event was waiting in the queue.
            return
        except Exception:
            logger.warning("watcher: failed to process %s", path, exc_info=True)
            return

    def _process_deleted(self, path: Path) -> None:
        # Never turn a temporary loss of the NAS root into mass index deletion.
        # A stale delete event is also ignored if the path exists again.
        if not self._indexer.images_dir.is_dir() or path.exists():
            return
        self._indexer.delete_path(path)

    def _process_directory(self, path: Path) -> None:
        if not path.is_dir():
            return
        try:
            images = sorted(
                candidate
                for candidate in path.rglob("*")
                if candidate.is_file() and candidate.suffix.lower() in IMAGE_EXTENSIONS
            )
        except OSError:
            logger.warning("watcher: failed to scan new directory %s", path, exc_info=True)
            return
        for image_path in images:
            self._process_changed(image_path)

    def _process_deleted_directory(self, path: Path) -> None:
        if not self._indexer.images_dir.is_dir() or path.exists():
            return
        self._indexer.delete_directory(path)


class RealtimeWatcher:
    """Fast event capture plus one serialized model-processing worker."""

    def __init__(self, indexer: Indexer):
        self._queue: queue.Queue[tuple[_WorkKind, Path] | None] = queue.Queue()
        self._pending: set[tuple[_WorkKind, str]] = set()
        self._pending_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._handler = _Handler(indexer, self.submit)
        self._observer = Observer()
        self._observer.schedule(self._handler, str(indexer.images_dir), recursive=True)
        self._worker = threading.Thread(
            target=self._run_worker, name="imagefind-nas-indexer", daemon=True
        )
        self._worker.start()
        try:
            self._observer.start()
        except Exception:
            self.stop()
            self.join(timeout=5)
            raise

    def submit(self, kind: _WorkKind, path: Path) -> None:
        if self._stop_event.is_set():
            return
        key = (kind, str(path))
        with self._pending_lock:
            if key in self._pending:
                return
            self._pending.add(key)
        self._queue.put((kind, path))

    def _run_worker(self) -> None:
        while not self._stop_event.is_set():
            item = self._queue.get()
            if item is None or self._stop_event.is_set():
                return
            kind, path = item
            with self._pending_lock:
                self._pending.discard((kind, str(path)))
            try:
                self._handler.process(kind, path)
            except Exception:
                logger.warning("watcher: unexpected %s failure for %s", kind, path, exc_info=True)

    def stop(self) -> None:
        if self._stop_event.is_set():
            return
        self._stop_event.set()
        self._observer.stop()
        self._queue.put(None)

    def join(self, timeout: float | None = None) -> None:
        self._observer.join(timeout=timeout)
        self._worker.join(timeout=timeout)


def start_watcher(indexer: Indexer) -> RealtimeWatcher:
    if not indexer.images_dir.is_dir():
        raise FileNotFoundError(f"images_dir {indexer.images_dir} is not reachable")
    return RealtimeWatcher(indexer)


def start_reconciliation_loop(
    indexer: Indexer,
    job_factory: Callable[[], "ReindexJob"],
    interval_seconds: float,
    stop_event: threading.Event,
    can_run: Callable[[], bool] | None = None,
    run_lock: LockType | None = None,
) -> threading.Thread:
    def loop():
        while not stop_event.wait(interval_seconds):
            if can_run is not None and not can_run():
                continue
            job = job_factory()
            try:
                if run_lock is None:
                    indexer.run_reindex(job, confirm_deletions=True)
                else:
                    with run_lock:
                        indexer.run_reindex(job, confirm_deletions=True)
                if job.error:
                    logger.warning("reconciliation failed: %s", job.error)
            except Exception:
                logger.warning("reconciliation: run_reindex failed", exc_info=True)

    thread = threading.Thread(
        target=loop, name="imagefind-reconciliation", daemon=True
    )
    thread.start()
    return thread
