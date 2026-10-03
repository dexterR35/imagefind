import threading
from pathlib import Path

import numpy as np
import torch
from PIL import Image
from ram import get_transform, inference_ram as _ram_inference
from ram.models import ram_plus

from . import config, embeddings
from .image_utils import flatten_to_rgb
from .model_download import verify_ram_checkpoint

_device = "cuda" if torch.cuda.is_available() else "cpu"
_ram_model = None
_ram_transform = None
_ram_default_class_threshold = None
_ram_load_error: Exception | None = None
_ram_load_error_signature: tuple[bool, int, int] | None = None
_load_lock = threading.Lock()
# Loading, inference, and unloading must not overlap. In particular, clearing
# the process-wide model while a watcher thread is using it could make another
# thread load a second 3 GB copy before the first one has actually died.
_inference_lock = threading.Lock()
_tag_embedding_cache: dict[str, tuple[np.ndarray, list[np.ndarray]]] = {}
_tag_cache_lock = threading.Lock()
_REFERENCE_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


def _load_rgb(image_path: Path) -> Image.Image:
    with Image.open(image_path) as raw:
        return flatten_to_rgb(raw)


_RAM_CHECKPOINT_URL = (
    "https://huggingface.co/xinyu1205/recognize-anything-plus-model/blob/main/ram_plus_swin_large_14m.pth"
)


def _checkpoint_signature() -> tuple[bool, int, int]:
    try:
        stat = config.RAM_CHECKPOINT_PATH.stat()
        return True, stat.st_size, stat.st_mtime_ns
    except OSError:
        return False, 0, 0


def _get_ram():
    global _ram_model, _ram_transform, _ram_default_class_threshold
    global _ram_load_error, _ram_load_error_signature
    if _ram_model is None:
        # Double-checked locking: the reindex background thread and a
        # /search-triggered request thread can both race to lazy-load the
        # model on first use, so the actual load must happen under a lock,
        # with the outer unlocked check kept only as a fast path afterward.
        with _load_lock:
            if _ram_model is None:
                signature = _checkpoint_signature()
                if _ram_load_error is not None and signature == _ram_load_error_signature:
                    # Re-raise a cached failure while the checkpoint is still
                    # unchanged, but automatically retry after install or
                    # replacement without requiring a backend restart.
                    raise _ram_load_error
                if signature != _ram_load_error_signature:
                    _ram_load_error = None
                    _ram_load_error_signature = None
                if not config.RAM_CHECKPOINT_PATH.is_file():
                    _ram_load_error = FileNotFoundError(
                        f"RAM++ checkpoint not found at '{config.RAM_CHECKPOINT_PATH}'. "
                        f"Download ram_plus_swin_large_14m.pth from {_RAM_CHECKPOINT_URL} "
                        "and place it there before indexing."
                    )
                    _ram_load_error_signature = signature
                    raise _ram_load_error
                if not verify_ram_checkpoint():
                    _ram_load_error = RuntimeError(
                        f"RAM++ checkpoint at '{config.RAM_CHECKPOINT_PATH}' failed its pinned "
                        "SHA-256 verification. Delete it and install the verified model again."
                    )
                    _ram_load_error_signature = signature
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
                    _ram_load_error_signature = _checkpoint_signature()
                    raise _ram_load_error from exc
                # Saved once here so a later conf override can be undone: the
                # checkpoint's per-tag tuned thresholds only exist in this one
                # in-memory copy, nowhere else to recover them from otherwise.
                _ram_default_class_threshold = model.class_threshold.clone()
                _ram_model = model
                _ram_load_error = None
                _ram_load_error_signature = None
    return _ram_transform, _ram_model


def ensure_ram_ready() -> None:
    """Eagerly loads (and caches) the RAM++ model so a missing or corrupt
    checkpoint fails once, up front, with an actionable message - instead of
    being discovered only on the first image of a reindex run and then
    silently retried on every subsequent image."""
    _get_ram()


def unload_ram_model() -> None:
    """Release RAM++ and PyTorch's now-unused CUDA cache.

    The embedding model and EasyOCR have their own process-wide models and deliberately stay
    loaded; this only drops the large tagger that is not needed for search
    while the indexer is idle.
    """
    global _ram_model, _ram_transform, _ram_default_class_threshold
    with _inference_lock:
        with _load_lock:
            model = _ram_model
            _ram_model = None
            _ram_transform = None
            # This clone lives on the same device as the model, so it must be
            # cleared too (even though it is tiny compared with the weights).
            _ram_default_class_threshold = None
        del model
        if torch.cuda.is_available():
            # PyTorch otherwise keeps the peak inference blocks reserved for
            # this process, which makes Task Manager report high idle VRAM.
            torch.cuda.empty_cache()


def detect_ram_objects(
    image_path: Path, conf: float | None = None, *, image: Image.Image | None = None
) -> list[str]:
    # conf stays None by default rather than reading config.RAM_CONFIDENCE at call
    # time being the only option — RAM++'s checkpoint ships with per-tag tuned
    # thresholds, so leaving it None means "use those", and only an explicit value
    # (from settings or a caller) overrides every tag's threshold uniformly.
    #
    # `image`, when supplied, is the already-opened, EXIF-corrected, display-
    # ready RGB image the indexer decoded once for the whole pipeline; it is
    # equivalent to _load_rgb(image_path) but avoids re-reading the file.
    if conf is None:
        conf = config.RAM_CONFIDENCE
    with _inference_lock:
        transform, model = _get_ram()
        rgb = image if image is not None else _load_rgb(image_path)
        tensor = transform(rgb).unsqueeze(0).to(_device)
        # Always explicitly set the threshold (override or restore) rather than only
        # mutating it when conf is not None — model.class_threshold is a shared,
        # mutable array on the process-lifetime singleton model, so a previous call's
        # override would otherwise silently stick around forever once conf goes back
        # to None, even though that's supposed to mean "use the model's own defaults".
        #
        # `conf` is applied as a floor on the checkpoint's per-tag tuned
        # thresholds, not a flat replacement: RAM++ ships thresholds calibrated
        # per class, so raising every under-confident tag to at least `conf`
        # trims noise while keeping that calibration shape intact. A flat
        # replace made easy tags under-fire and rare tags over-fire.
        model.class_threshold = (
            torch.clamp(_ram_default_class_threshold, min=conf) if conf is not None
            else _ram_default_class_threshold
        )
        with torch.no_grad():
            tags, _ = _ram_inference(tensor, model)
    denylist = config.RAM_TAG_DENYLIST
    return sorted({
        tag for raw in tags.split("|")
        if (tag := raw.strip()) and tag.lower() not in denylist
    })


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


def _get_tag_targets(tag: str) -> tuple[np.ndarray, list[np.ndarray]]:
    """The tag's text embedding plus any reference-image embeddings found in
    RAM_CUSTOM_TAG_REFERENCE_DIR/<tag>/ - a few real example pictures anchor a
    specific named entity (e.g. "zeus") far better than the word alone.

    They are kept apart, not averaged: image-to-image cosines run several
    times higher than text-to-image ones (~0.55 for unrelated icon sheets vs.
    ~0.08 for a matching word), so a blended prototype pushed every image over
    the text threshold the moment a single reference picture existed."""
    with _tag_cache_lock:
        cached = _tag_embedding_cache.get(tag)
    if cached is not None:
        return cached
    targets = (embeddings.embed_text(tag), _load_reference_embeddings(tag))
    with _tag_cache_lock:
        _tag_embedding_cache[tag] = targets
    return targets


def reference_embeddings(tag: str) -> list[np.ndarray]:
    """Embeddings of the tag's reference pictures (cached), [] if it has none."""
    return _get_tag_targets(tag)[1]


def detect_custom_tags(
    image_embedding: np.ndarray,
    custom_tags: list[str],
    threshold: float | None = None,
    reference_threshold: float | None = None,
    use_references: bool = True,
) -> list[str]:
    """Custom tags whose word matches the image (cosine >= threshold) or whose
    reference pictures look like it (cosine >= reference_threshold with any
    of them; skipped with use_references=False, when the indexer matches the
    references against objects inside the image instead). Reuses the image embedding already computed for the similarity
    index, so the only extra cost is one text embedding per tag (cached).

    Not RAM++'s own open-set mode, which needs a separate CLIP package and
    swaps out the whole tag vocabulary rather than adding to it."""
    if threshold is None:
        threshold = config.RAM_CUSTOM_TAG_THRESHOLD
    if reference_threshold is None:
        reference_threshold = config.RAM_CUSTOM_TAG_REFERENCE_THRESHOLD
    if not custom_tags:
        return []
    matched = []
    for tag in custom_tags:
        text, references = _get_tag_targets(tag)
        if not use_references:
            references = []
        if embeddings.cosine_similarity(image_embedding, text) >= threshold or any(
            embeddings.cosine_similarity(image_embedding, reference) >= reference_threshold
            for reference in references
        ):
            matched.append(tag)
    return sorted(set(matched))
