import uuid
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from . import colors as colors_mod
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
    done: bool = False
    error: str | None = None


class Indexer:
    def __init__(self, images_dir: Path, index_dir: Path, store: IndexStore, vocabulary: list[str]):
        self.images_dir = Path(images_dir)
        self.index_dir = Path(index_dir)
        self.store = store
        self.vocabulary = vocabulary

    def process_image(self, path: Path) -> tuple[ImageEntry, np.ndarray]:
        stat = path.stat()
        existing = self.store.get_by_path(str(path))
        image_id = existing.id if existing else uuid.uuid4().hex

        thumb_path = self.index_dir / "thumbnails" / f"{image_id}.jpg"
        thumbnails.make_thumbnail(path, thumb_path)

        with Image.open(path) as img:
            img = img.convert("RGBA")
            color_names = colors_mod.extract_dominant_colors(img)
            embedding = embeddings.embed_image(img)

        text = ocr.extract_text(path)
        object_labels = objects_mod.detect_all_objects(path, self.vocabulary)

        entry = ImageEntry(
            id=image_id, path=str(path), thumbnail_path=str(thumb_path),
            ocr_text=text, colors=color_names, objects=object_labels,
            mtime=stat.st_mtime, size=stat.st_size,
        )
        return entry, embedding

    def run_reindex(self, job: ReindexJob) -> None:
        try:
            paths = [
                p for p in sorted(self.images_dir.rglob("*"))
                if p.suffix.lower() in IMAGE_EXTENSIONS
            ]
            job.total = len(paths)
            for path in paths:
                try:
                    if self.store.needs_reindex(path):
                        entry, embedding = self.process_image(path)
                        self.store.upsert(entry, embedding)
                except Exception as exc:
                    print(f"skipping {path}: {exc}")
                job.processed += 1
            self.store.save()
        except Exception as exc:
            job.error = str(exc)
        finally:
            job.done = True
