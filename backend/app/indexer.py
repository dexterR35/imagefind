import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

from . import colors as colors_mod
from . import config
from . import embeddings
from . import image_utils
from . import objects as objects_mod
from . import ocr
from . import thumbnails
from .storage import ImageEntry, IndexStore

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}

logger = logging.getLogger(__name__)


@dataclass
class ReindexJob:
    id: str
    total: int = 0
    processed: int = 0
    failed: int = 0
    done: bool = False
    error: str | None = None
    cancelled: bool = False
    # Not exposed to API callers directly - set via Indexer.cancel(job).
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False, compare=False)


@dataclass
class ReindexSettings:
    """A snapshot of the tunable settings, taken once at the start of a
    reindex run, so a POST /settings call arriving mid-run can't make one
    run process some images with old values and some with new ones."""

    ram_confidence: float | None
    custom_tags: list[str]
    custom_tag_threshold: float
    color_clusters: int
    color_min_share: float


class Indexer:
    def __init__(self, images_dir: Path, index_dir: Path, store: IndexStore, custom_tags: list[str] | None = None):
        self.images_dir = Path(images_dir)
        self.index_dir = Path(index_dir)
        self.store = store
        self.custom_tags = custom_tags if custom_tags is not None else []
        # All model pipelines share process-wide GPU/CPU state. Serializing
        # one image at a time also makes a watcher event and a reconciliation
        # scan atomically re-check needs_reindex() before doing expensive work.
        self._processing_lock = threading.RLock()
        self._last_reconcile_missing: set[str] = set()

    def _current_settings(self) -> ReindexSettings:
        return ReindexSettings(
            ram_confidence=config.RAM_CONFIDENCE,
            custom_tags=self.custom_tags,
            custom_tag_threshold=config.RAM_CUSTOM_TAG_THRESHOLD,
            color_clusters=config.COLOR_CLUSTERS,
            color_min_share=config.COLOR_MIN_SHARE,
        )

    def process_image(self, path: Path, settings: ReindexSettings) -> tuple[ImageEntry, np.ndarray]:
        stat = path.stat()
        existing = self.store.get_by_path(str(path))
        image_id = existing.id if existing else uuid.uuid4().hex

        thumb_path = self.index_dir / "thumbnails" / f"{image_id}.jpg"
        temporary_thumb = thumb_path.with_suffix(".jpg.tmp")
        try:
            thumbnails.make_thumbnail(path, temporary_thumb)

            with Image.open(path) as img:
                # Captured before .convert() below, which returns a new Image
                # object with .format unset and no EXIF - both must be read off
                # the freshly-opened original.
                width, height = img.size
                img_format = img.format or path.suffix.lstrip(".").upper()
                date_taken = image_utils.extract_date_taken(img, fallback=stat.st_mtime)

                img = img.convert("RGBA")
                color_names = colors_mod.extract_dominant_colors(
                    img, k=settings.color_clusters, min_share=settings.color_min_share
                )
                embedding = embeddings.embed_image(img)

            text = ocr.extract_text(path)
            object_labels = set(objects_mod.detect_ram_objects(path, conf=settings.ram_confidence))
            if settings.custom_tags:
                object_labels |= set(
                    objects_mod.detect_custom_tags(
                        embedding, settings.custom_tags, settings.custom_tag_threshold
                    )
                )
            object_labels = sorted(object_labels)

            entry = ImageEntry(
                id=image_id, path=str(path), thumbnail_path=str(thumb_path),
                ocr_text=text, colors=color_names, objects=object_labels,
                mtime=stat.st_mtime, size=stat.st_size,
                width=width, height=height, format=img_format,
                date_taken=date_taken, indexed_at=time.time(),
            )
            os.replace(temporary_thumb, thumb_path)
            return entry, embedding
        finally:
            temporary_thumb.unlink(missing_ok=True)

    def index_path_if_needed(
        self,
        path: Path,
        settings: ReindexSettings | None = None,
        force: bool = False,
        persist: bool = True,
    ) -> bool:
        """Index one path exactly once across watcher/reindex threads."""
        with self._processing_lock:
            if not force and not self.store.needs_reindex(path):
                return False
            existing = self.store.get_by_path(str(path))
            entry, embedding = self.process_image(path, settings or self._current_settings())
            try:
                self.store.upsert(entry, embedding)
                if persist:
                    # Watcher events index one file outside a batch and must
                    # survive a process restart immediately. Full reindexes
                    # opt out and retain their periodic 50-image commits.
                    self.store.save()
            except Exception:
                if existing is None:
                    self._remove_thumbnails([entry.thumbnail_path])
                raise
            return True

    def _remove_thumbnails(self, thumbnails: list[str]) -> None:
        thumbnail_root = (self.index_dir / "thumbnails").resolve()
        for thumbnail_path in thumbnails:
            try:
                thumbnail = Path(thumbnail_path).resolve()
                if thumbnail_root == thumbnail.parent or thumbnail_root in thumbnail.parents:
                    thumbnail.unlink(missing_ok=True)
            except OSError:
                logger.warning("failed to remove thumbnail %s", thumbnail_path, exc_info=True)

    def delete_path(self, path: Path) -> bool:
        with self._processing_lock:
            removed_thumbnail = self.store.delete_by_path(str(path))
            if removed_thumbnail is None:
                return False
            self._remove_thumbnails([removed_thumbnail])
            return True

    def delete_directory(self, path: Path) -> int:
        with self._processing_lock:
            removed_thumbnails = self.store.delete_under_directory(str(path))
            self._remove_thumbnails(removed_thumbnails)
            return len(removed_thumbnails)

    def cleanup_orphan_thumbnails(self) -> int:
        """Remove cached files that no database row can serve."""
        with self._processing_lock:
            thumbnail_root = (self.index_dir / "thumbnails").resolve()
            if not thumbnail_root.is_dir():
                return 0
            referenced = {
                Path(path).resolve() for path in self.store.all_thumbnail_paths()
            }
            removed = 0
            for thumbnail in thumbnail_root.iterdir():
                if (
                    not thumbnail.is_file()
                    or (thumbnail.suffix.lower() != ".jpg" and not thumbnail.name.endswith(".jpg.tmp"))
                    or thumbnail.resolve() in referenced
                ):
                    continue
                try:
                    thumbnail.unlink()
                    removed += 1
                except OSError:
                    logger.warning("failed to remove orphan thumbnail %s", thumbnail, exc_info=True)
            return removed

    def _list_image_paths(self) -> list[Path]:
        if not self.images_dir.is_dir():
            return []
        errors: list[OSError] = []
        paths: list[Path] = []
        for root, dirnames, filenames in os.walk(
            self.images_dir, onerror=errors.append
        ):
            dirnames.sort()
            for filename in sorted(filenames):
                path = Path(root) / filename
                if path.suffix.lower() in IMAGE_EXTENSIONS:
                    paths.append(path)
        if errors:
            raise OSError(f"incomplete image scan: {errors[0]}")
        return sorted(paths)

    def run_reindex(
        self, job: ReindexJob, force: bool = False, confirm_deletions: bool = False
    ) -> None:
        try:
            settings = self._current_settings()
            # Every reindex re-embeds custom tags from scratch, so adding or
            # changing reference images for a tag between runs actually takes
            # effect instead of silently reusing a stale cached embedding.
            objects_mod.clear_custom_tag_cache()
            paths = self._list_image_paths()
            job.total = len(paths)
            ram_ready = False
            for path in paths:
                if job.cancel_event.is_set():
                    job.cancelled = True
                    break
                try:
                    should_index = force or self.store.needs_reindex(path)
                except Exception as exc:
                    job.failed += 1
                    logger.warning("skipping %s: %s", path, exc)
                    should_index = False
                if should_index:
                    # Load RAM++ only when the scan actually finds work. Keep
                    # readiness outside the per-image error handler so a bad
                    # checkpoint still fails the whole job once, clearly.
                    if not ram_ready:
                        objects_mod.ensure_ram_ready()
                        ram_ready = True
                    try:
                        self.index_path_if_needed(path, settings, force=force, persist=False)
                    except Exception as exc:
                        job.failed += 1
                        logger.warning("skipping %s: %s", path, exc)
                job.processed += 1
                if job.processed % 50 == 0:
                    self.store.save()
            if job.cancelled:
                # Whatever was already indexed this run stays indexed - the
                # next reindex picks back up via needs_reindex() - but skip
                # the prune/final-save pass below since it belongs to a full,
                # completed scan.
                self.store.save()
                return
            # images_dir may be a network mount (e.g. a NAS share) that can
            # disappear out from under the app - Path.rglob() on a missing
            # directory silently returns [] rather than raising, which would
            # otherwise look identical to "the folder is genuinely empty" and
            # prune() would then wipe every existing entry. Abort instead.
            if not self.images_dir.is_dir():
                raise RuntimeError(f"images_dir {self.images_dir} is not reachable - skipping prune")
            # Re-list rather than reusing `paths`: a file deleted mid-scan
            # (after being listed but before its turn in the loop) must not
            # survive pruning just because it was present at the start.
            current_paths = {str(p) for p in self._list_image_paths()}
            if confirm_deletions:
                # NAS directory listings can be incomplete during a brief SMB
                # interruption even while the share root still exists. Only
                # delete a path after two clean reconciliation scans agree it
                # is absent. Real filesystem delete events remain immediate.
                known_paths = self.store.all_paths()
                missing = known_paths - current_paths
                confirmed_missing = missing & self._last_reconcile_missing
                keep_paths = current_paths | (missing - confirmed_missing)
                removed_thumbnails = self.store.prune(keep_paths)
                self._last_reconcile_missing = missing
            else:
                removed_thumbnails = self.store.prune(current_paths)
                self._last_reconcile_missing.clear()
            self._remove_thumbnails(removed_thumbnails)
            self.store.save()
        except Exception as exc:
            job.error = str(exc)
        finally:
            try:
                # RAM++ is only needed while indexing. Drop its large model
                # and PyTorch's cached peak allocations so the app does not
                # occupy most of the GPU while it is sitting idle. The next
                # changed image or reindex run will lazily load it again.
                objects_mod.unload_ram_model()
            except Exception:
                # Cleanup must never turn an otherwise successful reindex into
                # a failed job; log it while preserving the original outcome.
                logger.warning("failed to unload RAM++ after reindex", exc_info=True)
            finally:
                job.done = True
