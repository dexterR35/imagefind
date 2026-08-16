import threading
from pathlib import Path

import numpy as np
import torch
from PIL import Image
from ram import get_transform, inference_ram as _ram_inference
from ram.models import ram_plus

from . import config, embeddings
from .image_utils import flatten_to_rgb

_device = "cuda" if torch.cuda.is_available() else "cpu"
_ram_model = None
_ram_transform = None
_ram_default_class_threshold = None
_ram_load_error: Exception | None = None
_load_lock = threading.Lock()
_tag_embedding_cache: dict[str, np.ndarray] = {}
_tag_cache_lock = threading.Lock()
_REFERENCE_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


def _load_rgb(image_path: Path) -> Image.Image:
    with Image.open(image_path) as raw:
        return flatten_to_rgb(raw)


_RAM_CHECKPOINT_URL = (
    "https://huggingface.co/xinyu1205/recognize-anything-plus-model/blob/main/ram_plus_swin_large_14m.pth"
)


def _get_ram():
    global _ram_model, _ram_transform, _ram_default_class_threshold, _ram_load_error
    if _ram_load_error is not None:
        # A previous attempt already failed (missing/corrupt checkpoint) -
        # re-raise the same clear error instead of retrying (and re-failing)
        # a slow model load on every single image in a reindex run.
        raise _ram_load_error
    if _ram_model is None:
        # Double-checked locking: the reindex background thread and a
        # /search-triggered request thread can both race to lazy-load the
        # model on first use, so the actual load must happen under a lock,
        # with the outer unlocked check kept only as a fast path afterward.
        with _load_lock:
            if _ram_model is None and _ram_load_error is None:
                if not config.RAM_CHECKPOINT_PATH.is_file():
                    _ram_load_error = FileNotFoundError(
                        f"RAM++ checkpoint not found at '{config.RAM_CHECKPOINT_PATH}'. "
                        f"Download ram_plus_swin_large_14m.pth from {_RAM_CHECKPOINT_URL} "
                        "and place it there before indexing."
                    )
                    raise _ram_load_error
                try:
                    _ram_transform = get_transform(image_size=config.RAM_IMAGE_SIZE)
                    model = ram_plus(
                        pretrained=str(config.RAM_CHECKPOINT_PATH),
                        image_size=config.RAM_IMAGE_SIZE,
                        vit="swin_l",
                    )
                    model = model.eval().to(_device)
                except Exception as exc:
                    _ram_load_error = RuntimeError(
                        f"RAM++ checkpoint at '{config.RAM_CHECKPOINT_PATH}' failed to load: {exc}. "
                        f"It may be incomplete or corrupt - re-download it from {_RAM_CHECKPOINT_URL}."
                    )
                    raise _ram_load_error from exc
                # Saved once here so a later conf override can be undone: the
                # checkpoint's per-tag tuned thresholds only exist in this one
                # in-memory copy, nowhere else to recover them from otherwise.
                _ram_default_class_threshold = model.class_threshold.clone()
                _ram_model = model
    return _ram_transform, _ram_model


def ensure_ram_ready() -> None:
    """Eagerly loads (and caches) the RAM++ model so a missing or corrupt
    checkpoint fails once, up front, with an actionable message - instead of
    being discovered only on the first image of a reindex run and then
    silently retried on every subsequent image."""
    _get_ram()


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
    # Always explicitly set the threshold (override or restore) rather than only
    # mutating it when conf is not None — model.class_threshold is a shared,
    # mutable array on the process-lifetime singleton model, so a previous call's
    # override would otherwise silently stick around forever once conf goes back
    # to None, even though that's supposed to mean "use the model's own defaults".
    model.class_threshold = (
        torch.ones_like(model.class_threshold) * conf if conf is not None
        else _ram_default_class_threshold
    )
    with torch.no_grad():
        tags, _ = _ram_inference(tensor, model)
    return sorted({t.strip() for t in tags.split("|") if t.strip()})


def clear_custom_tag_cache() -> None:
    """Called at the start of every reindex run so a tag's embedding is always
    recomputed fresh — otherwise adding/changing reference images for an
    already-cached tag would silently keep using the stale prototype vector
    until the backend process itself restarted."""
    with _tag_cache_lock:
        _tag_embedding_cache.clear()


def _load_reference_embeddings(tag: str) -> list[np.ndarray]:
    # Defense in depth against a custom tag containing path-traversal segments
    # (e.g. "../../Desktop") or an absolute path that would otherwise make the
    # `/` join below escape RAM_CUSTOM_TAG_REFERENCE_DIR entirely — resolve
    # both sides and verify tag_dir is genuinely inside the reference root
    # before ever touching the filesystem. SettingsUpdate already rejects
    # tags like this at the API boundary, but this holds regardless of how
    # a tag value got into config.RAM_CUSTOM_TAGS.
    base = config.RAM_CUSTOM_TAG_REFERENCE_DIR.resolve()
    tag_dir = (base / tag).resolve()
    if tag_dir == base or base not in tag_dir.parents:
        return []
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
