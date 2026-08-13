import json
import logging
import os
import threading
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class ImageEntry:
    id: str
    path: str
    thumbnail_path: str
    ocr_text: str
    colors: list[str]
    objects: list[str]
    mtime: float
    size: int


class IndexStore:
    def __init__(self, index_dir: Path, embedding_dim: int = 512):
        self.index_dir = Path(index_dir)
        self.index_dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.index_dir / "index.json"
        self.embeddings_path = self.index_dir / "embeddings.npy"
        self.embedding_dim = embedding_dim
        self.entries: list[ImageEntry] = []
        self.embeddings: np.ndarray = np.zeros((0, embedding_dim), dtype=np.float32)
        self._by_id: dict[str, int] = {}
        self._by_path: dict[str, int] = {}
        self.lock = threading.RLock()

    def load(self) -> None:
        with self.lock:
            if self.index_path.exists():
                data = json.loads(self.index_path.read_text())
                self.entries = [ImageEntry(**e) for e in data]
            else:
                self.entries = []
            if self.embeddings_path.exists():
                self.embeddings = np.load(self.embeddings_path)
            else:
                self.embeddings = np.zeros((0, self.embedding_dim), dtype=np.float32)

            if len(self.entries) != self.embeddings.shape[0]:
                logger.warning(
                    "IndexStore: entries/embeddings length mismatch (%d entries vs %d "
                    "embeddings) — resetting index to empty",
                    len(self.entries), self.embeddings.shape[0],
                )
                self.entries = []
                self.embeddings = np.zeros((0, self.embedding_dim), dtype=np.float32)

            self._reindex_lookup()

    def _reindex_lookup(self) -> None:
        self._by_id = {e.id: i for i, e in enumerate(self.entries)}
        self._by_path = {e.path: i for i, e in enumerate(self.entries)}

    def save(self) -> None:
        with self.lock:
            tmp_index = self.index_path.with_suffix(".json.tmp")
            tmp_index.write_text(json.dumps([asdict(e) for e in self.entries]))
            os.replace(tmp_index, self.index_path)

            tmp_emb = self.embeddings_path.with_suffix(".tmp.npy")
            np.save(tmp_emb, self.embeddings)
            os.replace(tmp_emb, self.embeddings_path)

    def needs_reindex(self, path: Path) -> bool:
        with self.lock:
            key = str(path)
            if key not in self._by_path:
                return True
            entry = self.entries[self._by_path[key]]
        stat = Path(path).stat()
        return entry.mtime != stat.st_mtime or entry.size != stat.st_size

    def upsert(self, entry: ImageEntry, embedding: np.ndarray) -> None:
        with self.lock:
            if entry.path in self._by_path:
                i = self._by_path[entry.path]
                self.entries[i] = entry
                self.embeddings[i] = embedding
            else:
                self.entries.append(entry)
                self.embeddings = np.vstack([self.embeddings, embedding[None, :]])
            self._reindex_lookup()

    def prune(self, keep_paths: set[str]) -> None:
        """Remove entries whose path is not in keep_paths, keeping entries/embeddings aligned."""
        with self.lock:
            keep_indices = [i for i, e in enumerate(self.entries) if e.path in keep_paths]
            self.entries = [self.entries[i] for i in keep_indices]
            if keep_indices:
                self.embeddings = self.embeddings[keep_indices]
            else:
                self.embeddings = np.zeros((0, self.embedding_dim), dtype=np.float32)
            self._reindex_lookup()

    def get(self, id: str) -> ImageEntry | None:
        with self.lock:
            i = self._by_id.get(id)
            return self.entries[i] if i is not None else None

    def get_by_path(self, path: str) -> ImageEntry | None:
        with self.lock:
            i = self._by_path.get(path)
            return self.entries[i] if i is not None else None

    def get_embedding(self, id: str) -> np.ndarray | None:
        with self.lock:
            i = self._by_id.get(id)
            return self.embeddings[i] if i is not None else None

    def all(self) -> list[ImageEntry]:
        with self.lock:
            return list(self.entries)
