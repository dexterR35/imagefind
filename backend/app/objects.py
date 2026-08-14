import threading
from pathlib import Path

import numpy as np
import torch
from PIL import Image
from ram import get_transform, inference_ram as _ram_inference
from ram.models import ram_plus

from . import config, embeddings

_device = "cuda" if torch.cuda.is_available() else "cpu"
_ram_model = None
_ram_transform = None
_load_lock = threading.Lock()
_tag_embedding_cache: dict[str, np.ndarray] = {}
_tag_cache_lock = threading.Lock()
_REFERENCE_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


def _load_rgb(image_path: Path) -> Image.Image:
    with Image.open(image_path) as raw:
        if raw.mode in ("RGBA", "LA", "P"):
            rgba = raw.convert("RGBA")
            bg = Image.new("RGB", rgba.size, (255, 255, 255))
            bg.paste(rgba, mask=rgba.getchannel("A"))
            return bg
        return raw.convert("RGB")


def _get_ram():
    global _ram_model, _ram_transform
    if _ram_model is None:
        # Double-checked locking: the reindex background thread and a
        # /search-triggered request thread can both race to lazy-load the
        # model on first use, so the actual load must happen under a lock,
        # with the outer unlocked check kept only as a fast path afterward.
        with _load_lock:
            if _ram_model is None:
                _ram_transform = get_transform(image_size=config.RAM_IMAGE_SIZE)
                model = ram_plus(
                    pretrained=str(config.RAM_CHECKPOINT_PATH),
                    image_size=config.RAM_IMAGE_SIZE,
                    vit="swin_l",
                )
                _ram_model = model.eval().to(_device)
    return _ram_transform, _ram_model


def detect_ram_objects(image_path: Path, conf: float | None = None) -> list[str]:
    # conf stays None by default rather than reading config.RAM_CONFIDENCE at call
    # time being the only option — RAM++'s checkpoint ships with per-tag tuned
    # thresholds, so leaving it None means "use those", and only an explicit value
    # (from settings or a caller) overrides every tag's threshold uniformly.
    if conf is None:
        conf = config.RAM_CONFIDENCE
    transform, model = _get_ram()
    image = _load_rgb(image_path)
    tensor = transform(image).unsqueeze(0).to(_device)
    if conf is not None:
        model.class_threshold = torch.ones_like(model.class_threshold) * conf
    with torch.no_grad():
        tags, _ = _ram_inference(tensor, model)
    return sorted({t.strip() for t in tags.split("|") if t.strip()})


def _load_reference_embeddings(tag: str) -> list[np.ndarray]:
    tag_dir = config.RAM_CUSTOM_TAG_REFERENCE_DIR / tag
    if not tag_dir.is_dir():
        return []
    vectors = []
    for p in sorted(tag_dir.iterdir()):
        if p.suffix.lower() not in _REFERENCE_IMAGE_EXTENSIONS:
            continue
        try:
            with Image.open(p) as img:
                vectors.append(embeddings.embed_image(img))
        except Exception:
            # An unreadable/corrupt reference photo shouldn't take down tag
            # matching for every image in the library — skip it and keep going.
            continue
    return vectors


def _get_tag_embedding(tag: str) -> np.ndarray:
    # Blends the bare text embedding with any reference-image embeddings found in
    # RAM_CUSTOM_TAG_REFERENCE_DIR/<tag>/ into a single averaged, re-normalized
    # "prototype" vector — a few real example photos anchor a specific named
    # entity (e.g. "zeus") far better than the word alone. With no reference
    # images present, this is identical to the old text-only behavior.
    with _tag_cache_lock:
        cached = _tag_embedding_cache.get(tag)
    if cached is not None:
        return cached
    vectors = [embeddings.embed_text(tag), *_load_reference_embeddings(tag)]
    centroid = np.mean(vectors, axis=0).astype(np.float32)
    centroid = centroid / np.linalg.norm(centroid)
    with _tag_cache_lock:
        _tag_embedding_cache[tag] = centroid
    return centroid


def detect_custom_tags(
    image_embedding: np.ndarray, custom_tags: list[str], threshold: float | None = None
) -> list[str]:
    # Not RAM++'s own open-set mode (that needs a separate CLIP package and
    # swaps out the whole tag vocabulary rather than adding to it) — this
    # reuses the CLIP model already loaded for text search in embeddings.py,
    # matching each custom word against the image embedding that's already
    # computed for the similarity index, so there's no extra image encoding
    # cost per image, only one small text embedding per distinct tag (cached).
    if threshold is None:
        threshold = config.RAM_CUSTOM_TAG_THRESHOLD
    if not custom_tags:
        return []
    matched = [
        tag for tag in custom_tags
        if embeddings.cosine_similarity(image_embedding, _get_tag_embedding(tag)) >= threshold
    ]
    return sorted(set(matched))
