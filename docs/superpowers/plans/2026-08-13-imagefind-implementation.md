# ImageFind Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local mini-app that indexes a folder of images and lets you search them by semantic meaning, on-image text (OCR), color, and detected objects (standard + brand-specific), plus "Find Similar."

**Architecture:** A Python/FastAPI backend runs the ML pipeline (OCR, CLIP embeddings, color extraction, object detection) over a configured local folder and stores results in two flat files (no database). A React/Vite frontend calls the backend's REST API to render three independent search filters and a results grid.

**Tech Stack:** Python 3.10+, FastAPI, PyTorch (CUDA), open_clip, EasyOCR, ultralytics (YOLOv8), Hugging Face `transformers` (OWL-ViT v2), OpenCV + scikit-learn, numpy; React + Vite + TypeScript, Vitest + React Testing Library.

**Spec:** `docs/superpowers/specs/2026-08-13-imagefind-design.md`

## Global Constraints

- No database — storage is exactly two files: `.index/index.json` + `.index/embeddings.npy`
- No NAS mounting, auth, or hosting concerns — reads from a local `IMAGES_DIR` folder path
- No SAM / pixel segmentation — bounding-box-level object detection is enough
- Color extraction must ignore transparent pixels (alpha-aware)
- Reindexing must skip files whose size + mtime are unchanged since the last index
- A single corrupt/unreadable image must never abort the whole reindex batch — log and continue
- The open-vocabulary object term list must be a plain, editable config value, not hardcoded inside a function

## Implementation Notes

The spec names **Grounding DINO** for open-vocabulary object detection. This plan uses
**OWL-ViT v2** (`google/owlv2-base-patch16-ensemble`, via Hugging Face `transformers`)
instead. It gives the same capability the spec asks for — text-prompted detection of
things that aren't COCO classes, like "clover" or "horseshoe" — but installs as a
normal pip package with no custom CUDA extension to compile and no manual checkpoint
download from GitHub releases, which is where Grounding DINO's reference
implementation commonly breaks. If you'd rather use the original Grounding DINO repo,
say so before Task 7 — everything else in this plan is unaffected by that choice.

---

## Task 1: Backend project scaffolding

**Files:**
- Create: `backend/requirements.txt`
- Create: `backend/app/__init__.py`
- Create: `backend/app/main.py`
- Test: `backend/tests/test_main.py`

**Interfaces:**
- Produces: FastAPI `app` object importable as `from app.main import app`, with a
  `GET /health` endpoint returning `{"status": "ok"}`

- [ ] **Step 1: Create the Python environment and install dependencies**

```bash
mkdir -p backend/app backend/tests
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
nvidia-smi
```

Note the CUDA version `nvidia-smi` reports (e.g. "CUDA Version: 12.4"), then install
the matching PyTorch build (this must happen *before* `requirements.txt`, which
deliberately excludes torch/torchvision so pip doesn't silently resolve a CPU-only
wheel over it):

```bash
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121
python -c "import torch; print(torch.cuda.is_available())"
```

This must print `True`. If it prints `False`, the CUDA index URL doesn't match your
driver — check https://pytorch.org/get-started/locally/ for the right one.

- [ ] **Step 2: Write requirements.txt**

```
fastapi==0.115.0
uvicorn[standard]==0.32.0
python-multipart==0.0.12
numpy==1.26.4
pillow==10.4.0
opencv-python-headless==4.10.0.84
scikit-learn==1.5.2
open-clip-torch==2.26.1
easyocr==1.7.2
ultralytics==8.3.0
transformers==4.44.2
pytest==8.3.3
httpx==0.27.2
```

```bash
pip install -r requirements.txt
```

- [ ] **Step 3: Write the failing test**

`backend/tests/test_main.py`:

```python
from fastapi.testclient import TestClient
from app.main import app


def test_health_returns_ok():
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

- [ ] **Step 4: Run test to verify it fails**

```bash
cd backend
python -m pytest tests/test_main.py -v
```

Expected: FAIL (`ModuleNotFoundError: No module named 'app.main'` or similar).

- [ ] **Step 5: Write minimal implementation**

`backend/app/__init__.py`: empty file.

`backend/app/main.py`:

```python
from fastapi import FastAPI

app = FastAPI(title="ImageFind")


@app.get("/health")
def health():
    return {"status": "ok"}
```

- [ ] **Step 6: Run test to verify it passes**

```bash
python -m pytest tests/test_main.py -v
```

Expected: PASS

- [ ] **Step 7: Commit**

```bash
cd ..
git add backend/requirements.txt backend/app/__init__.py backend/app/main.py backend/tests/test_main.py
git commit -m "feat(backend): scaffold FastAPI app with health check"
```

---

## Task 2: Storage layer (flat-file index)

**Files:**
- Create: `backend/app/storage.py`
- Test: `backend/tests/test_storage.py`

**Interfaces:**
- Produces:
  - `ImageEntry` dataclass: `id, path, thumbnail_path, ocr_text, colors: list[str], objects: list[str], mtime: float, size: int`
  - `IndexStore(index_dir: Path, embedding_dim: int = 512)` with methods:
    - `load() -> None`
    - `save() -> None`
    - `needs_reindex(path: Path) -> bool`
    - `upsert(entry: ImageEntry, embedding: np.ndarray) -> None`
    - `get(id: str) -> ImageEntry | None`
    - `get_by_path(path: str) -> ImageEntry | None`
    - `get_embedding(id: str) -> np.ndarray | None`
    - `all() -> list[ImageEntry]`
    - `.embeddings: np.ndarray` (rows aligned with `.entries` order)

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_storage.py`:

```python
import numpy as np

from app.storage import ImageEntry, IndexStore


def _entry(path="/imgs/a.png", mtime=0.0, size=0):
    return ImageEntry(
        id="a1", path=path, thumbnail_path="/thumbs/a1.jpg",
        ocr_text="NETBET", colors=["green"], objects=["clover"],
        mtime=mtime, size=size,
    )


def test_upsert_save_load_roundtrip(tmp_path):
    store = IndexStore(tmp_path, embedding_dim=4)
    store.load()
    store.upsert(_entry(), np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32))
    store.save()

    reloaded = IndexStore(tmp_path, embedding_dim=4)
    reloaded.load()
    assert reloaded.get("a1").ocr_text == "NETBET"
    assert reloaded.get_embedding("a1").tolist() == [1.0, 0.0, 0.0, 0.0]
    assert reloaded.get_by_path("/imgs/a.png").id == "a1"


def test_needs_reindex_detects_new_and_unchanged_files(tmp_path):
    img_path = tmp_path / "photo.png"
    img_path.write_bytes(b"fake-image-bytes")
    stat = img_path.stat()

    store = IndexStore(tmp_path / "idx", embedding_dim=4)
    store.load()
    assert store.needs_reindex(img_path) is True

    entry = _entry(path=str(img_path), mtime=stat.st_mtime, size=stat.st_size)
    store.upsert(entry, np.zeros(4, dtype=np.float32))
    assert store.needs_reindex(img_path) is False


def test_needs_reindex_true_when_file_changes(tmp_path):
    img_path = tmp_path / "photo.png"
    img_path.write_bytes(b"v1")
    stat = img_path.stat()
    store = IndexStore(tmp_path / "idx", embedding_dim=4)
    store.load()
    entry = _entry(path=str(img_path), mtime=stat.st_mtime, size=stat.st_size)
    store.upsert(entry, np.zeros(4, dtype=np.float32))

    img_path.write_bytes(b"v2-longer-content")
    assert store.needs_reindex(img_path) is True


def test_upsert_replaces_existing_entry_for_same_path(tmp_path):
    store = IndexStore(tmp_path, embedding_dim=4)
    store.load()
    store.upsert(_entry(path="/imgs/a.png"), np.array([1.0, 0, 0, 0], dtype=np.float32))
    store.upsert(_entry(path="/imgs/a.png"), np.array([0, 1.0, 0, 0], dtype=np.float32))
    assert len(store.all()) == 1
    assert store.get("a1") is not None
    assert store.get_embedding("a1").tolist() == [0, 1.0, 0, 0]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_storage.py -v
```

Expected: FAIL (`ModuleNotFoundError: No module named 'app.storage'`)

- [ ] **Step 3: Write implementation**

`backend/app/storage.py`:

```python
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np


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

    def load(self) -> None:
        if self.index_path.exists():
            data = json.loads(self.index_path.read_text())
            self.entries = [ImageEntry(**e) for e in data]
        else:
            self.entries = []
        if self.embeddings_path.exists():
            self.embeddings = np.load(self.embeddings_path)
        else:
            self.embeddings = np.zeros((0, self.embedding_dim), dtype=np.float32)
        self._reindex_lookup()

    def _reindex_lookup(self) -> None:
        self._by_id = {e.id: i for i, e in enumerate(self.entries)}
        self._by_path = {e.path: i for i, e in enumerate(self.entries)}

    def save(self) -> None:
        tmp_index = self.index_path.with_suffix(".json.tmp")
        tmp_index.write_text(json.dumps([asdict(e) for e in self.entries]))
        os.replace(tmp_index, self.index_path)

        tmp_emb = self.embeddings_path.with_suffix(".tmp.npy")
        np.save(tmp_emb, self.embeddings)
        os.replace(tmp_emb, self.embeddings_path)

    def needs_reindex(self, path: Path) -> bool:
        key = str(path)
        if key not in self._by_path:
            return True
        entry = self.entries[self._by_path[key]]
        stat = Path(path).stat()
        return entry.mtime != stat.st_mtime or entry.size != stat.st_size

    def upsert(self, entry: ImageEntry, embedding: np.ndarray) -> None:
        if entry.path in self._by_path:
            i = self._by_path[entry.path]
            self.entries[i] = entry
            self.embeddings[i] = embedding
        else:
            self.entries.append(entry)
            self.embeddings = np.vstack([self.embeddings, embedding[None, :]])
        self._reindex_lookup()

    def get(self, id: str) -> ImageEntry | None:
        i = self._by_id.get(id)
        return self.entries[i] if i is not None else None

    def get_by_path(self, path: str) -> ImageEntry | None:
        i = self._by_path.get(path)
        return self.entries[i] if i is not None else None

    def get_embedding(self, id: str) -> np.ndarray | None:
        i = self._by_id.get(id)
        return self.embeddings[i] if i is not None else None

    def all(self) -> list[ImageEntry]:
        return list(self.entries)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_storage.py -v
```

Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
cd ..
git add backend/app/storage.py backend/tests/test_storage.py
git commit -m "feat(backend): add flat-file index storage layer"
```

---

## Task 3: Color extraction

**Files:**
- Create: `backend/app/colors.py`
- Test: `backend/tests/test_colors.py`

**Interfaces:**
- Produces: `extract_dominant_colors(image: PIL.Image.Image, k: int = 4) -> list[str]`,
  `_hsv_to_name(h: float, s: float, v: float) -> str`

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_colors.py`:

```python
from PIL import Image, ImageDraw

from app.colors import _hsv_to_name, extract_dominant_colors


def test_hsv_to_name_basic_colors():
    assert _hsv_to_name(0.0, 0.9, 0.9) == "red"
    assert _hsv_to_name(0.33, 0.9, 0.9) == "green"
    assert _hsv_to_name(0.6, 0.9, 0.9) == "blue"
    assert _hsv_to_name(0.0, 0.0, 0.95) == "white"
    assert _hsv_to_name(0.0, 0.0, 0.05) == "black"


def test_extract_dominant_colors_solid_red():
    img = Image.new("RGBA", (30, 30), (220, 20, 20, 255))
    assert extract_dominant_colors(img) == ["red"]


def test_extract_dominant_colors_ignores_transparent_pixels():
    img = Image.new("RGBA", (40, 40), (0, 255, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rectangle([10, 10, 29, 29], fill=(0, 200, 0, 255))
    assert extract_dominant_colors(img) == ["green"]


def test_extract_dominant_colors_finds_multiple_colors():
    img = Image.new("RGBA", (40, 20), (220, 20, 20, 255))
    draw = ImageDraw.Draw(img)
    draw.rectangle([20, 0, 39, 19], fill=(20, 20, 220, 255))
    result = extract_dominant_colors(img)
    assert set(result) == {"red", "blue"}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_colors.py -v
```

Expected: FAIL (`ModuleNotFoundError: No module named 'app.colors'`)

- [ ] **Step 3: Write implementation**

`backend/app/colors.py`:

```python
import colorsys

import numpy as np
from PIL import Image
from sklearn.cluster import KMeans

COLOR_NAMES = [
    "red", "orange", "yellow", "gold", "green", "blue", "purple",
    "pink", "brown", "black", "white", "gray",
]


def _hsv_to_name(h: float, s: float, v: float) -> str:
    if v < 0.12:
        return "black"
    if s < 0.10 and v > 0.85:
        return "white"
    if s < 0.15:
        return "gray"

    deg = h * 360
    if s < 0.35 and v < 0.6 and 15 <= deg < 50:
        return "brown"
    if deg < 12 or deg >= 348:
        return "red"
    if deg < 40:
        return "orange"
    if deg < 65:
        return "gold" if s > 0.55 and v > 0.55 else "yellow"
    if deg < 170:
        return "green"
    if deg < 255:
        return "blue"
    if deg < 300:
        return "purple"
    return "pink"


def extract_dominant_colors(image: Image.Image, k: int = 4, min_share: float = 0.08) -> list[str]:
    rgba = image.convert("RGBA")
    arr = np.asarray(rgba).reshape(-1, 4)
    opaque = arr[arr[:, 3] > 10]
    if len(opaque) == 0:
        return []

    rgb = opaque[:, :3].astype(np.float32) / 255.0
    if len(rgb) > 20000:
        idx = np.random.default_rng(0).choice(len(rgb), 20000, replace=False)
        rgb = rgb[idx]

    k_eff = min(k, len(rgb))
    km = KMeans(n_clusters=k_eff, n_init=4, random_state=0).fit(rgb)
    counts = np.bincount(km.labels_)

    names: list[str] = []
    for center, count in sorted(zip(km.cluster_centers_, counts), key=lambda c: -c[1]):
        share = count / len(rgb)
        if share < min_share:
            continue
        h, s, v = colorsys.rgb_to_hsv(*center)
        name = _hsv_to_name(h, s, v)
        if name not in names:
            names.append(name)
    return names
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_colors.py -v
```

Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
cd ..
git add backend/app/colors.py backend/tests/test_colors.py
git commit -m "feat(backend): add alpha-aware dominant color extraction"
```

---

## Task 4: Thumbnail generation

**Files:**
- Create: `backend/app/thumbnails.py`
- Test: `backend/tests/test_thumbnails.py`

**Interfaces:**
- Produces: `make_thumbnail(src_path: Path, dest_path: Path, max_size: int = 320) -> None`

- [ ] **Step 1: Write the failing test**

`backend/tests/test_thumbnails.py`:

```python
from PIL import Image

from app.thumbnails import make_thumbnail


def test_make_thumbnail_resizes_and_creates_parent_dirs(tmp_path):
    src = tmp_path / "src.png"
    Image.new("RGB", (800, 600), (10, 20, 30)).save(src)
    dest = tmp_path / "nested" / "thumb.jpg"

    make_thumbnail(src, dest, max_size=320)

    assert dest.exists()
    with Image.open(dest) as thumb:
        assert max(thumb.size) <= 320
        assert thumb.size[0] / thumb.size[1] == 800 / 600
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_thumbnails.py -v
```

Expected: FAIL (`ModuleNotFoundError: No module named 'app.thumbnails'`)

- [ ] **Step 3: Write implementation**

`backend/app/thumbnails.py`:

```python
from pathlib import Path

from PIL import Image


def make_thumbnail(src_path: Path, dest_path: Path, max_size: int = 320) -> None:
    dest_path = Path(dest_path)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(src_path) as img:
        img = img.convert("RGB")
        img.thumbnail((max_size, max_size))
        img.save(dest_path, format="JPEG", quality=85)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python -m pytest tests/test_thumbnails.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd ..
git add backend/app/thumbnails.py backend/tests/test_thumbnails.py
git commit -m "feat(backend): add thumbnail generation"
```

---

## Task 5: OCR extraction

**Files:**
- Create: `backend/app/ocr.py`
- Test: `backend/tests/test_ocr.py`

**Interfaces:**
- Produces: `extract_text(image_path: Path) -> str`

- [ ] **Step 1: Write the failing test**

`backend/tests/test_ocr.py`:

```python
from PIL import Image, ImageDraw, ImageFont

from app.ocr import extract_text


def test_extract_text_reads_rendered_word(tmp_path):
    img = Image.new("RGB", (500, 180), "white")
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=64)
    draw.text((20, 50), "NETBET", fill="black", font=font)
    path = tmp_path / "text.png"
    img.save(path)

    result = extract_text(path)

    assert "NETBET" in result.upper()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_ocr.py -v
```

Expected: FAIL (`ModuleNotFoundError: No module named 'app.ocr'`)

- [ ] **Step 3: Write implementation**

`backend/app/ocr.py`:

```python
from pathlib import Path

import easyocr
import torch

_reader = None


def _get_reader():
    global _reader
    if _reader is None:
        _reader = easyocr.Reader(["en"], gpu=torch.cuda.is_available())
    return _reader


def extract_text(image_path: Path) -> str:
    results = _get_reader().readtext(str(image_path), detail=0)
    return " ".join(results).strip()
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python -m pytest tests/test_ocr.py -v
```

Expected: PASS. First run downloads EasyOCR's detection/recognition weights
(~65MB) — this needs internet access once.

- [ ] **Step 5: Commit**

```bash
cd ..
git add backend/app/ocr.py backend/tests/test_ocr.py
git commit -m "feat(backend): add OCR text extraction via EasyOCR"
```

---

## Task 6: CLIP embeddings

**Files:**
- Create: `backend/app/embeddings.py`
- Test: `backend/tests/test_embeddings.py`

**Interfaces:**
- Produces:
  - `embed_image(image: PIL.Image.Image) -> np.ndarray` (unit-normalized, shape `(512,)`, dtype `float32`)
  - `embed_text(text: str) -> np.ndarray` (same shape/dtype)
  - `cosine_similarity(a: np.ndarray, b: np.ndarray) -> float`

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_embeddings.py`:

```python
import numpy as np
from PIL import Image

from app.embeddings import cosine_similarity, embed_image, embed_text


def test_embed_image_returns_unit_vector():
    img = Image.new("RGB", (224, 224), (200, 30, 30))
    vec = embed_image(img)
    assert vec.shape == (512,)
    assert vec.dtype == np.float32
    assert abs(np.linalg.norm(vec) - 1.0) < 1e-3


def test_text_embedding_matches_matching_color_image_better():
    red_img = Image.new("RGB", (224, 224), (220, 20, 20))
    blue_img = Image.new("RGB", (224, 224), (20, 20, 220))
    red_emb = embed_image(red_img)
    blue_emb = embed_image(blue_img)
    text_emb = embed_text("a solid red square")

    assert cosine_similarity(text_emb, red_emb) > cosine_similarity(text_emb, blue_emb)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_embeddings.py -v
```

Expected: FAIL (`ModuleNotFoundError: No module named 'app.embeddings'`)

- [ ] **Step 3: Write implementation**

`backend/app/embeddings.py`:

```python
import numpy as np
import open_clip
import torch
from PIL import Image

_device = "cuda" if torch.cuda.is_available() else "cpu"
_model = None
_preprocess = None
_tokenizer = None


def _load():
    global _model, _preprocess, _tokenizer
    if _model is None:
        _model, _, _preprocess = open_clip.create_model_and_transforms(
            "ViT-B-32", pretrained="openai"
        )
        _model = _model.to(_device).eval()
        _tokenizer = open_clip.get_tokenizer("ViT-B-32")
    return _model, _preprocess, _tokenizer


def embed_image(image: Image.Image) -> np.ndarray:
    model, preprocess, _ = _load()
    tensor = preprocess(image.convert("RGB")).unsqueeze(0).to(_device)
    with torch.no_grad():
        features = model.encode_image(tensor)
        features = features / features.norm(dim=-1, keepdim=True)
    return features.squeeze(0).cpu().numpy().astype(np.float32)


def embed_text(text: str) -> np.ndarray:
    model, _, tokenizer = _load()
    tokens = tokenizer([text]).to(_device)
    with torch.no_grad():
        features = model.encode_text(tokens)
        features = features / features.norm(dim=-1, keepdim=True)
    return features.squeeze(0).cpu().numpy().astype(np.float32)


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b))
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_embeddings.py -v
```

Expected: PASS. First run downloads the CLIP ViT-B-32 checkpoint (~350MB).

- [ ] **Step 5: Commit**

```bash
cd ..
git add backend/app/embeddings.py backend/tests/test_embeddings.py
git commit -m "feat(backend): add CLIP image/text embeddings"
```

---

## Task 7: Object detection (YOLO + OWL-ViT open-vocabulary)

**Files:**
- Create: `backend/app/objects.py`
- Test: `backend/tests/test_objects.py`

**Interfaces:**
- Produces:
  - `detect_yolo_objects(image_path: Path, conf: float = 0.4) -> list[str]`
  - `detect_vocab_objects(image_path: Path, vocabulary: list[str], conf: float = 0.15) -> list[str]`
  - `detect_all_objects(image_path: Path, vocabulary: list[str]) -> list[str]` (deduplicated, sorted union of both)

- [ ] **Step 1: Write the failing test**

`backend/tests/test_objects.py`:

```python
from PIL import Image

from app.objects import detect_all_objects


def test_detect_all_objects_returns_deduped_sorted_list(tmp_path):
    # A synthetic blank image won't reliably trigger real detections from either
    # model; this test verifies the pipeline runs end-to-end without erroring and
    # returns the right contract (list[str], deduplicated, sorted).
    img = Image.new("RGB", (416, 416), (200, 200, 200))
    path = tmp_path / "blank.png"
    img.save(path)

    result = detect_all_objects(path, vocabulary=["clover", "horseshoe"])

    assert isinstance(result, list)
    assert result == sorted(set(result))
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_objects.py -v
```

Expected: FAIL (`ModuleNotFoundError: No module named 'app.objects'`)

- [ ] **Step 3: Write implementation**

`backend/app/objects.py`:

```python
from pathlib import Path

import torch
from PIL import Image
from transformers import Owlv2ForObjectDetection, Owlv2Processor
from ultralytics import YOLO

_device = "cuda" if torch.cuda.is_available() else "cpu"
_yolo_model = None
_owl_processor = None
_owl_model = None


def _get_yolo():
    global _yolo_model
    if _yolo_model is None:
        _yolo_model = YOLO("yolov8n.pt")
    return _yolo_model


def detect_yolo_objects(image_path: Path, conf: float = 0.4) -> list[str]:
    model = _get_yolo()
    results = model.predict(source=str(image_path), conf=conf, device=_device, verbose=False)
    labels = set()
    for r in results:
        for c in r.boxes.cls.tolist():
            labels.add(model.names[int(c)])
    return sorted(labels)


def _get_owl():
    global _owl_processor, _owl_model
    if _owl_model is None:
        _owl_processor = Owlv2Processor.from_pretrained("google/owlv2-base-patch16-ensemble")
        _owl_model = (
            Owlv2ForObjectDetection.from_pretrained("google/owlv2-base-patch16-ensemble")
            .to(_device)
            .eval()
        )
    return _owl_processor, _owl_model


def detect_vocab_objects(image_path: Path, vocabulary: list[str], conf: float = 0.15) -> list[str]:
    if not vocabulary:
        return []
    processor, model = _get_owl()
    image = Image.open(image_path).convert("RGB")
    inputs = processor(text=[vocabulary], images=image, return_tensors="pt").to(_device)
    with torch.no_grad():
        outputs = model(**inputs)
    target_sizes = torch.tensor([image.size[::-1]])
    results = processor.post_process_object_detection(
        outputs=outputs, threshold=conf, target_sizes=target_sizes
    )[0]
    labels = {vocabulary[i] for i in results["labels"].tolist()}
    return sorted(labels)


def detect_all_objects(image_path: Path, vocabulary: list[str]) -> list[str]:
    found = set(detect_yolo_objects(image_path)) | set(detect_vocab_objects(image_path, vocabulary))
    return sorted(found)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python -m pytest tests/test_objects.py -v
```

Expected: PASS. First run downloads YOLOv8n weights (~6MB) and the OWL-ViT v2
checkpoint (~600MB).

- [ ] **Step 5: Commit**

```bash
cd ..
git add backend/app/objects.py backend/tests/test_objects.py
git commit -m "feat(backend): add YOLO + open-vocabulary object detection"
```

---

## Task 8: Indexer orchestration

**Files:**
- Create: `backend/app/indexer.py`
- Test: `backend/tests/test_indexer.py`

**Interfaces:**
- Consumes: `IndexStore`, `ImageEntry` (Task 2); `extract_dominant_colors` (Task 3);
  `make_thumbnail` (Task 4); `extract_text` (Task 5); `embed_image` (Task 6);
  `detect_all_objects` (Task 7)
- Produces:
  - `ReindexJob` dataclass: `id: str, total: int = 0, processed: int = 0, done: bool = False, error: str | None = None`
  - `Indexer(images_dir: Path, index_dir: Path, store: IndexStore, vocabulary: list[str])` with:
    - `process_image(path: Path) -> tuple[ImageEntry, np.ndarray]`
    - `run_reindex(job: ReindexJob) -> None`

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_indexer.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_indexer.py -v
```

Expected: FAIL (`ModuleNotFoundError: No module named 'app.indexer'`)

- [ ] **Step 3: Write implementation**

`backend/app/indexer.py`:

```python
import uuid
from dataclasses import dataclass
from pathlib import Path

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

    def process_image(self, path: Path):
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
                if self.store.needs_reindex(path):
                    try:
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_indexer.py -v
```

Expected: PASS (2 tests). This exercises the full ML pipeline on real (tiny)
images, so it will take longer than the earlier tests — that's expected.

- [ ] **Step 5: Commit**

```bash
cd ..
git add backend/app/indexer.py backend/tests/test_indexer.py
git commit -m "feat(backend): add indexer orchestrating the full pipeline"
```

---

## Task 9: Search and Find Similar

**Files:**
- Create: `backend/app/search.py`
- Test: `backend/tests/test_search.py`

**Interfaces:**
- Consumes: `IndexStore`, `ImageEntry` (Task 2); `embed_text`, `cosine_similarity` (Task 6)
- Produces:
  - `search(store: IndexStore, text: str | None = None, color: str | None = None, obj: str | None = None, limit: int = 60) -> list[ImageEntry]`
  - `find_similar(store: IndexStore, image_id: str, limit: int = 20) -> list[ImageEntry]`

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_search.py`:

```python
import numpy as np

from app import embeddings
from app.search import find_similar, search
from app.storage import ImageEntry, IndexStore


def _entry(id, colors=None, objects=None, ocr_text=""):
    return ImageEntry(
        id=id, path=f"/imgs/{id}.png", thumbnail_path=f"/t/{id}.jpg",
        ocr_text=ocr_text, colors=colors or [], objects=objects or [],
        mtime=0.0, size=0,
    )


def _store_with(tmp_path, entries_and_vecs):
    store = IndexStore(tmp_path, embedding_dim=2)
    store.load()
    for entry, vec in entries_and_vecs:
        store.upsert(entry, np.array(vec, dtype=np.float32))
    return store


def test_search_filters_by_color_and_object_with_and_logic(tmp_path):
    store = _store_with(tmp_path, [
        (_entry("a", colors=["green"], objects=["clover"]), [1, 0]),
        (_entry("b", colors=["green"], objects=["person"]), [1, 0]),
        (_entry("c", colors=["blue"], objects=["clover"]), [1, 0]),
    ])
    result = search(store, color="green", obj="clover")
    assert [e.id for e in result] == ["a"]


def test_search_text_ranks_ocr_match_above_unrelated(tmp_path, monkeypatch):
    store = _store_with(tmp_path, [
        (_entry("a", ocr_text="NETBET BONUS"), [1.0, 0.0]),
        (_entry("b", ocr_text="unrelated"), [0.0, 1.0]),
    ])
    monkeypatch.setattr(embeddings, "embed_text", lambda q: np.array([1.0, 0.0], dtype=np.float32))
    result = search(store, text="netbet")
    assert [e.id for e in result] == ["a", "b"]


def test_find_similar_excludes_self_and_orders_by_similarity(tmp_path):
    store = _store_with(tmp_path, [
        (_entry("a"), [1.0, 0.0]),
        (_entry("b"), [0.9, 0.1]),
        (_entry("c"), [0.0, 1.0]),
    ])
    result = find_similar(store, "a")
    assert [e.id for e in result] == ["b", "c"]


def test_find_similar_unknown_id_returns_empty(tmp_path):
    store = _store_with(tmp_path, [(_entry("a"), [1.0, 0.0])])
    assert find_similar(store, "missing") == []
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_search.py -v
```

Expected: FAIL (`ModuleNotFoundError: No module named 'app.search'`)

- [ ] **Step 3: Write implementation**

`backend/app/search.py`:

```python
from . import embeddings
from .storage import ImageEntry, IndexStore


def search(
    store: IndexStore,
    text: str | None = None,
    color: str | None = None,
    obj: str | None = None,
    limit: int = 60,
) -> list[ImageEntry]:
    entries = store.all()
    candidates = list(range(len(entries)))

    if color:
        candidates = [i for i in candidates if color in entries[i].colors]
    if obj:
        candidates = [i for i in candidates if obj in entries[i].objects]

    if text:
        text_lower = text.lower()
        text_matches = {i for i in candidates if text_lower in entries[i].ocr_text.lower()}
        query_embedding = embeddings.embed_text(text)
        scores = {}
        for i in candidates:
            sim = embeddings.cosine_similarity(query_embedding, store.embeddings[i])
            bonus = 0.25 if i in text_matches else 0.0
            scores[i] = sim + bonus
        ranked = sorted(candidates, key=lambda i: scores[i], reverse=True)
    else:
        ranked = candidates

    return [entries[i] for i in ranked[:limit]]


def find_similar(store: IndexStore, image_id: str, limit: int = 20) -> list[ImageEntry]:
    entry = store.get(image_id)
    if entry is None:
        return []
    query_embedding = store.get_embedding(image_id)
    entries = store.all()
    scored = []
    for i, other in enumerate(entries):
        if other.id == image_id:
            continue
        sim = embeddings.cosine_similarity(query_embedding, store.embeddings[i])
        scored.append((sim, i))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [entries[i] for _, i in scored[:limit]]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_search.py -v
```

Expected: PASS (4 tests). These do not load any ML model (the semantic-ranking
test monkeypatches `embed_text`), so they run fast.

- [ ] **Step 5: Commit**

```bash
cd ..
git add backend/app/search.py backend/tests/test_search.py
git commit -m "feat(backend): add combinable search filters and find-similar"
```

---

## Task 10: FastAPI endpoints wiring

**Files:**
- Create: `backend/app/config.py`
- Modify: `backend/app/main.py`
- Modify: `backend/tests/test_main.py`

**Interfaces:**
- Consumes: `IndexStore` (Task 2); `Indexer`, `ReindexJob` (Task 8); `search`,
  `find_similar` (Task 9)
- Produces endpoints: `GET /health`, `GET /search`, `GET /search/similar/{image_id}`,
  `POST /reindex`, `GET /reindex/status/{job_id}`, `GET /thumbnail/{image_id}`,
  `GET /colors`, `GET /objects`

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_main.py` (replace entire file):

```python
import importlib
import time

import numpy as np
from fastapi.testclient import TestClient


def _fresh_app(tmp_path, monkeypatch):
    images_dir = tmp_path / "images"
    images_dir.mkdir()
    monkeypatch.setenv("IMAGES_DIR", str(images_dir))
    monkeypatch.setenv("INDEX_DIR", str(tmp_path / "index"))
    from app import config, main
    importlib.reload(config)
    importlib.reload(main)
    return main, images_dir


def test_health_reports_zero_indexed(tmp_path, monkeypatch):
    main, _ = _fresh_app(tmp_path, monkeypatch)
    client = TestClient(main.app)
    assert client.get("/health").json() == {"status": "ok", "indexed": 0}


def test_reindex_on_empty_folder_completes_immediately(tmp_path, monkeypatch):
    main, _ = _fresh_app(tmp_path, monkeypatch)
    client = TestClient(main.app)
    job_id = client.post("/reindex").json()["job_id"]

    status = {}
    for _ in range(40):
        status = client.get(f"/reindex/status/{job_id}").json()
        if status["done"]:
            break
        time.sleep(0.05)

    assert status == {"processed": 0, "total": 0, "done": True, "error": None}


def test_search_and_filters_use_prepopulated_store(tmp_path, monkeypatch):
    main, _ = _fresh_app(tmp_path, monkeypatch)
    from app.storage import ImageEntry

    entry = ImageEntry(
        id="a1", path="/imgs/a.png", thumbnail_path=str(tmp_path / "a1.jpg"),
        ocr_text="NETBET", colors=["green"], objects=["clover"], mtime=0.0, size=0,
    )
    (tmp_path / "a1.jpg").write_bytes(b"fake-jpg-bytes")
    main.store.upsert(entry, np.ones(512, dtype=np.float32))

    client = TestClient(main.app)
    assert client.get("/colors").json() == ["green"]
    assert client.get("/objects").json() == ["clover"]

    result = client.get("/search", params={"color": "green"}).json()
    assert [r["id"] for r in result] == ["a1"]

    assert client.get("/search/similar/a1").json() == []
    assert client.get("/search/similar/missing").status_code == 404


def test_thumbnail_serves_cached_file(tmp_path, monkeypatch):
    main, _ = _fresh_app(tmp_path, monkeypatch)
    from app.storage import ImageEntry

    thumb_path = tmp_path / "thumb.jpg"
    thumb_path.write_bytes(b"fake-jpg-bytes")
    entry = ImageEntry(
        id="a1", path="/imgs/a.png", thumbnail_path=str(thumb_path),
        ocr_text="", colors=[], objects=[], mtime=0.0, size=0,
    )
    main.store.upsert(entry, np.ones(512, dtype=np.float32))

    client = TestClient(main.app)
    response = client.get("/thumbnail/a1")
    assert response.status_code == 200
    assert response.content == b"fake-jpg-bytes"
    assert client.get("/thumbnail/missing").status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_main.py -v
```

Expected: FAIL (`ModuleNotFoundError: No module named 'app.config'`, and
`/health` returning the old two-key-less shape).

- [ ] **Step 3: Write implementation**

`backend/app/config.py`:

```python
import os
from pathlib import Path

IMAGES_DIR = Path(os.environ.get("IMAGES_DIR", "./images"))
INDEX_DIR = Path(os.environ.get("INDEX_DIR", "./.index"))
VOCABULARY = [
    "clover", "horseshoe", "pot of gold", "coin", "dice",
    "hat", "logo", "trophy", "rainbow",
]
```

`backend/app/main.py` (replace entire file):

```python
import threading
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from . import config
from .indexer import Indexer, ReindexJob
from .search import find_similar as run_find_similar
from .search import search as run_search
from .storage import IndexStore

app = FastAPI(title="ImageFind")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

store = IndexStore(config.INDEX_DIR)
store.load()
indexer = Indexer(config.IMAGES_DIR, config.INDEX_DIR, store, config.VOCABULARY)
jobs: dict[str, ReindexJob] = {}


def _entry_to_dict(e) -> dict:
    return {
        "id": e.id, "path": e.path, "thumbnail_url": f"/thumbnail/{e.id}",
        "ocr_text": e.ocr_text, "colors": e.colors, "objects": e.objects,
    }


@app.get("/health")
def health():
    return {"status": "ok", "indexed": len(store.all())}


@app.get("/search")
def search_endpoint(text: str | None = None, color: str | None = None, object: str | None = None):
    results = run_search(store, text=text, color=color, obj=object)
    return [_entry_to_dict(e) for e in results]


@app.get("/search/similar/{image_id}")
def similar_endpoint(image_id: str):
    if store.get(image_id) is None:
        raise HTTPException(status_code=404, detail="image not found")
    results = run_find_similar(store, image_id)
    return [_entry_to_dict(e) for e in results]


@app.post("/reindex")
def reindex_endpoint():
    job = ReindexJob(id=uuid.uuid4().hex)
    jobs[job.id] = job
    thread = threading.Thread(target=indexer.run_reindex, args=(job,), daemon=True)
    thread.start()
    return {"job_id": job.id}


@app.get("/reindex/status/{job_id}")
def reindex_status(job_id: str):
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return {"processed": job.processed, "total": job.total, "done": job.done, "error": job.error}


@app.get("/thumbnail/{image_id}")
def thumbnail_endpoint(image_id: str):
    entry = store.get(image_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="image not found")
    return FileResponse(entry.thumbnail_path)


@app.get("/colors")
def colors_endpoint():
    return sorted({c for e in store.all() for c in e.colors})


@app.get("/objects")
def objects_endpoint():
    return sorted({o for e in store.all() for o in e.objects})
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_main.py -v
```

Expected: PASS (4 tests)

- [ ] **Step 5: Run the full backend test suite**

```bash
python -m pytest -v
```

Expected: PASS (all tests from Tasks 1-10)

- [ ] **Step 6: Commit**

```bash
cd ..
git add backend/app/config.py backend/app/main.py backend/tests/test_main.py
git commit -m "feat(backend): wire search, reindex, and thumbnail endpoints"
```

---

## Task 11: Frontend scaffolding and API client

**Files:**
- Create: `frontend/` (via Vite scaffold)
- Create: `frontend/src/api.ts`
- Test: `frontend/src/api.test.ts`

**Interfaces:**
- Produces:
  - `interface ImageResult { id, path, thumbnail_url, ocr_text, colors: string[], objects: string[] }`
  - `interface SearchFilters { text?, color?, object? }`
  - `interface ReindexStatus { processed, total, done, error }`
  - `fetchColors(): Promise<string[]>`
  - `fetchObjects(): Promise<string[]>`
  - `search(filters: SearchFilters): Promise<ImageResult[]>`
  - `findSimilar(imageId: string): Promise<ImageResult[]>`
  - `startReindex(): Promise<string>`
  - `fetchReindexStatus(jobId: string): Promise<ReindexStatus>`

- [ ] **Step 1: Scaffold the Vite project and test tooling**

```bash
npm create vite@latest frontend -- --template react-ts
cd frontend
npm install
npm install -D vitest @testing-library/react @testing-library/jest-dom jsdom
```

Add to `frontend/vite.config.ts`, inside `defineConfig({...})`:

```ts
test: {
  environment: "jsdom",
  setupFiles: "./src/setupTests.ts",
},
```

Create `frontend/src/setupTests.ts`:

```ts
import "@testing-library/jest-dom";
```

Add to `frontend/package.json` scripts: `"test": "vitest run"`.

- [ ] **Step 2: Write the failing test**

`frontend/src/api.test.ts`:

```ts
import { describe, expect, it, vi } from "vitest";
import { search } from "./api";

describe("search", () => {
  it("builds query params only for provided filters", async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      json: async () => [
        { id: "a1", path: "/x.png", thumbnail_url: "/thumbnail/a1", ocr_text: "", colors: [], objects: [] },
      ],
    });
    vi.stubGlobal("fetch", mockFetch);

    const results = await search({ color: "green" });

    expect(mockFetch).toHaveBeenCalledWith("http://localhost:8000/search?color=green");
    expect(results[0].id).toBe("a1");
  });
});
```

- [ ] **Step 3: Run test to verify it fails**

```bash
npm test
```

Expected: FAIL (`Cannot find module './api'`)

- [ ] **Step 4: Write implementation**

`frontend/src/api.ts`:

```ts
export interface ImageResult {
  id: string;
  path: string;
  thumbnail_url: string;
  ocr_text: string;
  colors: string[];
  objects: string[];
}

export interface SearchFilters {
  text?: string;
  color?: string;
  object?: string;
}

export interface ReindexStatus {
  processed: number;
  total: number;
  done: boolean;
  error: string | null;
}

const BASE_URL = "http://localhost:8000";

export async function fetchColors(): Promise<string[]> {
  const res = await fetch(`${BASE_URL}/colors`);
  return res.json();
}

export async function fetchObjects(): Promise<string[]> {
  const res = await fetch(`${BASE_URL}/objects`);
  return res.json();
}

export async function search(filters: SearchFilters): Promise<ImageResult[]> {
  const params = new URLSearchParams();
  if (filters.text) params.set("text", filters.text);
  if (filters.color) params.set("color", filters.color);
  if (filters.object) params.set("object", filters.object);
  const res = await fetch(`${BASE_URL}/search?${params.toString()}`);
  return res.json();
}

export async function findSimilar(imageId: string): Promise<ImageResult[]> {
  const res = await fetch(`${BASE_URL}/search/similar/${imageId}`);
  return res.json();
}

export async function startReindex(): Promise<string> {
  const res = await fetch(`${BASE_URL}/reindex`, { method: "POST" });
  const data = await res.json();
  return data.job_id;
}

export async function fetchReindexStatus(jobId: string): Promise<ReindexStatus> {
  const res = await fetch(`${BASE_URL}/reindex/status/${jobId}`);
  return res.json();
}
```

- [ ] **Step 5: Run test to verify it passes**

```bash
npm test
```

Expected: PASS

- [ ] **Step 6: Commit**

```bash
cd ..
git add frontend/
git commit -m "feat(frontend): scaffold Vite React app and backend API client"
```

---

## Task 12: SearchFilters component

**Files:**
- Create: `frontend/src/SearchFilters.tsx`
- Test: `frontend/src/SearchFilters.test.tsx`

**Interfaces:**
- Consumes: `fetchColors`, `fetchObjects`, `SearchFilters` type (Task 11)
- Produces: `SearchFilters({ onChange: (filters: Filters) => void })` React component

- [ ] **Step 1: Write the failing test**

`frontend/src/SearchFilters.test.tsx`:

```tsx
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import * as api from "./api";
import { SearchFilters } from "./SearchFilters";

describe("SearchFilters", () => {
  it("loads colors/objects and reports combined filter changes", async () => {
    vi.spyOn(api, "fetchColors").mockResolvedValue(["green", "blue"]);
    vi.spyOn(api, "fetchObjects").mockResolvedValue(["clover", "person"]);
    const onChange = vi.fn();

    render(<SearchFilters onChange={onChange} />);

    await waitFor(() => expect(screen.getByLabelText("green")).toBeInTheDocument());

    fireEvent.click(screen.getByLabelText("green"));
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "clover" } });
    fireEvent.change(screen.getByPlaceholderText("Search text or meaning..."), {
      target: { value: "netbet" },
    });

    await waitFor(() =>
      expect(onChange).toHaveBeenLastCalledWith({ text: "netbet", color: "green", object: "clover" })
    );
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npm test
```

Expected: FAIL (`Cannot find module './SearchFilters'`)

- [ ] **Step 3: Write implementation**

`frontend/src/SearchFilters.tsx`:

```tsx
import { useEffect, useState } from "react";
import { fetchColors, fetchObjects, SearchFilters as Filters } from "./api";

interface Props {
  onChange: (filters: Filters) => void;
}

export function SearchFilters({ onChange }: Props) {
  const [colors, setColors] = useState<string[]>([]);
  const [objects, setObjects] = useState<string[]>([]);
  const [text, setText] = useState("");
  const [color, setColor] = useState<string | undefined>(undefined);
  const [object, setObject] = useState<string | undefined>(undefined);

  useEffect(() => {
    fetchColors().then(setColors);
    fetchObjects().then(setObjects);
  }, []);

  useEffect(() => {
    onChange({ text: text || undefined, color, object });
  }, [text, color, object]);

  return (
    <div className="search-filters">
      <input
        type="text"
        placeholder="Search text or meaning..."
        value={text}
        onChange={(e) => setText(e.target.value)}
      />
      <div className="color-swatches">
        {colors.map((c) => (
          <button
            key={c}
            type="button"
            aria-label={c}
            className={c === color ? "swatch selected" : "swatch"}
            style={{ backgroundColor: c }}
            onClick={() => setColor(color === c ? undefined : c)}
          />
        ))}
      </div>
      <select value={object ?? ""} onChange={(e) => setObject(e.target.value || undefined)}>
        <option value="">All objects</option>
        {objects.map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
      </select>
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
npm test
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd ..
git add frontend/src/SearchFilters.tsx frontend/src/SearchFilters.test.tsx
git commit -m "feat(frontend): add separated text/color/object search filters"
```

---

## Task 13: ImageGrid and ImageCard components

**Files:**
- Create: `frontend/src/ImageCard.tsx`
- Create: `frontend/src/ImageGrid.tsx`
- Test: `frontend/src/ImageGrid.test.tsx`

**Interfaces:**
- Consumes: `ImageResult` type (Task 11)
- Produces:
  - `ImageCard({ image: ImageResult, onClick: (image) => void })`
  - `ImageGrid({ images: ImageResult[], onSelect: (image) => void })`

- [ ] **Step 1: Write the failing test**

`frontend/src/ImageGrid.test.tsx`:

```tsx
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ImageGrid } from "./ImageGrid";
import type { ImageResult } from "./api";

const sample: ImageResult[] = [
  { id: "a1", path: "/imgs/clover.png", thumbnail_url: "/thumbnail/a1", ocr_text: "", colors: ["green"], objects: ["clover"] },
];

describe("ImageGrid", () => {
  it("renders a card per image and reports clicks", () => {
    const onSelect = vi.fn();
    render(<ImageGrid images={sample} onSelect={onSelect} />);
    fireEvent.click(screen.getByAltText("clover.png"));
    expect(onSelect).toHaveBeenCalledWith(sample[0]);
  });

  it("shows an empty state with no results", () => {
    render(<ImageGrid images={[]} onSelect={vi.fn()} />);
    expect(screen.getByText("No images match these filters.")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npm test
```

Expected: FAIL (`Cannot find module './ImageGrid'`)

- [ ] **Step 3: Write implementation**

`frontend/src/ImageCard.tsx`:

```tsx
import type { ImageResult } from "./api";

interface Props {
  image: ImageResult;
  onClick: (image: ImageResult) => void;
}

export function ImageCard({ image, onClick }: Props) {
  const filename = image.path.split("/").pop() ?? image.path;
  return (
    <button type="button" className="image-card" onClick={() => onClick(image)}>
      <img src={image.thumbnail_url} alt={filename} />
      <span className="filename">{filename}</span>
    </button>
  );
}
```

`frontend/src/ImageGrid.tsx`:

```tsx
import type { ImageResult } from "./api";
import { ImageCard } from "./ImageCard";

interface Props {
  images: ImageResult[];
  onSelect: (image: ImageResult) => void;
}

export function ImageGrid({ images, onSelect }: Props) {
  if (images.length === 0) {
    return <p className="empty-state">No images match these filters.</p>;
  }
  return (
    <div className="image-grid">
      {images.map((img) => (
        <ImageCard key={img.id} image={img} onClick={onSelect} />
      ))}
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
npm test
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd ..
git add frontend/src/ImageCard.tsx frontend/src/ImageGrid.tsx frontend/src/ImageGrid.test.tsx
git commit -m "feat(frontend): add image results grid"
```

---

## Task 14: ReindexButton component

**Files:**
- Create: `frontend/src/ReindexButton.tsx`
- Test: `frontend/src/ReindexButton.test.tsx`

**Interfaces:**
- Consumes: `startReindex`, `fetchReindexStatus`, `ReindexStatus` type (Task 11)
- Produces: `ReindexButton({ onComplete: () => void })`

- [ ] **Step 1: Write the failing test**

`frontend/src/ReindexButton.test.tsx`:

```tsx
import { render, screen, fireEvent } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "./api";
import { ReindexButton } from "./ReindexButton";

describe("ReindexButton", () => {
  beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: true }));
  afterEach(() => vi.useRealTimers());

  it("polls status until done then calls onComplete", async () => {
    vi.spyOn(api, "startReindex").mockResolvedValue("job1");
    const statusSpy = vi
      .spyOn(api, "fetchReindexStatus")
      .mockResolvedValueOnce({ processed: 1, total: 2, done: false, error: null })
      .mockResolvedValueOnce({ processed: 2, total: 2, done: true, error: null });
    const onComplete = vi.fn();

    render(<ReindexButton onComplete={onComplete} />);
    fireEvent.click(screen.getByText("Reindex"));

    await vi.advanceTimersByTimeAsync(500);
    await vi.advanceTimersByTimeAsync(500);

    expect(statusSpy).toHaveBeenCalledTimes(2);
    expect(onComplete).toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npm test
```

Expected: FAIL (`Cannot find module './ReindexButton'`)

- [ ] **Step 3: Write implementation**

`frontend/src/ReindexButton.tsx`:

```tsx
import { useRef, useState } from "react";
import { fetchReindexStatus, startReindex, type ReindexStatus } from "./api";

interface Props {
  onComplete: () => void;
}

export function ReindexButton({ onComplete }: Props) {
  const [status, setStatus] = useState<ReindexStatus | null>(null);
  const [running, setRunning] = useState(false);
  const pollRef = useRef<number | null>(null);

  async function handleClick() {
    setRunning(true);
    const jobId = await startReindex();
    pollRef.current = window.setInterval(async () => {
      const s = await fetchReindexStatus(jobId);
      setStatus(s);
      if (s.done) {
        window.clearInterval(pollRef.current!);
        setRunning(false);
        onComplete();
      }
    }, 500);
  }

  return (
    <div className="reindex">
      <button type="button" onClick={handleClick} disabled={running}>
        {running ? "Reindexing..." : "Reindex"}
      </button>
      {status && !status.done && (
        <span>
          {status.processed} / {status.total}
        </span>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
npm test
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd ..
git add frontend/src/ReindexButton.tsx frontend/src/ReindexButton.test.tsx
git commit -m "feat(frontend): add reindex trigger with progress polling"
```

---

## Task 15: ImageModal and full App wiring

**Files:**
- Create: `frontend/src/ImageModal.tsx`
- Modify: `frontend/src/App.tsx`
- Test: `frontend/src/App.test.tsx`

**Interfaces:**
- Consumes: `SearchFilters` component (Task 12), `ImageGrid` (Task 13),
  `ReindexButton` (Task 14), `search`, `findSimilar` (Task 11)
- Produces: `ImageModal({ image, onClose, onFindSimilar })`; default-exported `App`

- [ ] **Step 1: Write the failing test**

`frontend/src/App.test.tsx`:

```tsx
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import * as api from "./api";
import App from "./App";

describe("App", () => {
  it("runs a search on filter change and opens Find Similar results", async () => {
    vi.spyOn(api, "fetchColors").mockResolvedValue(["green"]);
    vi.spyOn(api, "fetchObjects").mockResolvedValue(["clover"]);
    const image = {
      id: "a1", path: "/imgs/clover.png", thumbnail_url: "/thumbnail/a1",
      ocr_text: "", colors: ["green"], objects: ["clover"],
    };
    vi.spyOn(api, "search").mockResolvedValue([image]);
    vi.spyOn(api, "findSimilar").mockResolvedValue([image]);

    render(<App />);
    fireEvent.change(await screen.findByPlaceholderText("Search text or meaning..."), {
      target: { value: "clover" },
    });

    await waitFor(() => expect(screen.getByAltText("clover.png")).toBeInTheDocument());

    fireEvent.click(screen.getByAltText("clover.png"));
    fireEvent.click(screen.getByText("Find Similar"));

    await waitFor(() => expect(api.findSimilar).toHaveBeenCalledWith("a1"));
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npm test
```

Expected: FAIL (no "Find Similar" text / old default App content)

- [ ] **Step 3: Write implementation**

`frontend/src/ImageModal.tsx`:

```tsx
import type { ImageResult } from "./api";

interface Props {
  image: ImageResult;
  onClose: () => void;
  onFindSimilar: (id: string) => void;
}

export function ImageModal({ image, onClose, onFindSimilar }: Props) {
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <img src={image.thumbnail_url} alt={image.path} />
        <p>{image.ocr_text}</p>
        <p>Colors: {image.colors.join(", ")}</p>
        <p>Objects: {image.objects.join(", ")}</p>
        <button type="button" onClick={() => onFindSimilar(image.id)}>
          Find Similar
        </button>
        <button type="button" onClick={onClose}>
          Close
        </button>
      </div>
    </div>
  );
}
```

`frontend/src/App.tsx` (replace entire file):

```tsx
import { useCallback, useState } from "react";
import { findSimilar, search, type ImageResult, type SearchFilters as Filters } from "./api";
import { ImageGrid } from "./ImageGrid";
import { ImageModal } from "./ImageModal";
import { ReindexButton } from "./ReindexButton";
import { SearchFilters } from "./SearchFilters";

export default function App() {
  const [images, setImages] = useState<ImageResult[]>([]);
  const [selected, setSelected] = useState<ImageResult | null>(null);
  const [filters, setFilters] = useState<Filters>({});

  const runSearch = useCallback(async (f: Filters) => {
    setFilters(f);
    const results = await search(f);
    setImages(results);
  }, []);

  async function handleFindSimilar(id: string) {
    const results = await findSimilar(id);
    setImages(results);
    setSelected(null);
  }

  return (
    <div className="app">
      <h1>ImageFind</h1>
      <SearchFilters onChange={runSearch} />
      <ReindexButton onComplete={() => runSearch(filters)} />
      <ImageGrid images={images} onSelect={setSelected} />
      {selected && (
        <ImageModal
          image={selected}
          onClose={() => setSelected(null)}
          onFindSimilar={handleFindSimilar}
        />
      )}
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
npm test
```

Expected: PASS

- [ ] **Step 5: Run the full frontend test suite**

```bash
npm test
```

Expected: PASS (all tests from Tasks 11-15)

- [ ] **Step 6: Commit**

```bash
cd ..
git add frontend/src/ImageModal.tsx frontend/src/App.tsx frontend/src/App.test.tsx
git commit -m "feat(frontend): wire filters, grid, reindex, and find-similar into App"
```

---

## Running the full app against your real images

```bash
# Terminal 1 — backend
cd backend
source .venv/bin/activate
IMAGES_DIR=/path/to/your/5k-images INDEX_DIR=./.index uvicorn app.main:app --reload --port 8000

# Terminal 2 — frontend
cd frontend
npm run dev
```

Open the Vite dev server URL, click **Reindex** (first run will be slow — it's
downloading model weights and processing every image), then try the searches from
the spec: a known clover image (`clover` in the object filter or text box), a known
green image (green swatch), a known "NetBet" banner (text box), and click a result
to try **Find Similar**.
