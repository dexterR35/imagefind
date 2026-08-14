import uuid
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from . import colors as colors_mod
from . import config
from . import embeddings
from . import objects as objects_mod
from . import ocr
from . import thumbnails
from .storage import ImageEntry, IndexStore

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


@dataclass
class ReindexJob:
    id: str
    total: int = 0
    processed: int = 0
    failed: int = 0
    done: bool = False
    error: str | None = None


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
        thumbnails.make_thumbnail(path, thumb_path)

        with Image.open(path) as img:
            img = img.convert("RGBA")
            color_names = colors_mod.extract_dominant_colors(
                img, k=settings.color_clusters, min_share=settings.color_min_share
            )
            embedding = embeddings.embed_image(img)

        text = ocr.extract_text(path)
        object_labels = set(objects_mod.detect_ram_objects(path, conf=settings.ram_confidence))
        if settings.custom_tags:
            object_labels |= set(
                objects_mod.detect_custom_tags(embedding, settings.custom_tags, settings.custom_tag_threshold)
            )
        object_labels = sorted(object_labels)

        entry = ImageEntry(
            id=image_id, path=str(path), thumbnail_path=str(thumb_path),
            ocr_text=text, colors=color_names, objects=object_labels,
            mtime=stat.st_mtime, size=stat.st_size,
        )
        return entry, embedding

    def _list_image_paths(self) -> list[Path]:
        return [
            p for p in sorted(self.images_dir.rglob("*"))
            if p.suffix.lower() in IMAGE_EXTENSIONS
        ]

    def run_reindex(self, job: ReindexJob, force: bool = False) -> None:
        try:
            settings = self._current_settings()
            # Every reindex re-embeds custom tags from scratch, so adding or
            # changing reference images for a tag between runs actually takes
            # effect instead of silently reusing a stale cached embedding.
            objects_mod.clear_custom_tag_cache()
            paths = self._list_image_paths()
            job.total = len(paths)
            for path in paths:
                try:
                    if force or self.store.needs_reindex(path):
                        entry, embedding = self.process_image(path, settings)
                        self.store.upsert(entry, embedding)
                except Exception as exc:
                    job.failed += 1
                    print(f"skipping {path}: {exc}")
                job.processed += 1
                if job.processed % 50 == 0:
                    self.store.save()
            # Re-list rather than reusing `paths`: a file deleted mid-scan
            # (after being listed but before its turn in the loop) must not
            # survive pruning just because it was present at the start.
            current_paths = {str(p) for p in self._list_image_paths()}
            self.store.prune(current_paths)
            self.store.save()
        except Exception as exc:
            job.error = str(exc)
        finally:
            job.done = True
