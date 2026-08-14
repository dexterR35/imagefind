import json
import os
from pathlib import Path

INDEX_DIR = Path(os.environ.get("INDEX_DIR", "./.index"))
SETTINGS_FILE = INDEX_DIR / "settings.json"


def _persisted_images_dir() -> Path | None:
    try:
        data = json.loads(SETTINGS_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    value = data.get("images_dir")
    return Path(value) if value else None


def save_images_dir(images_dir: Path) -> None:
    SETTINGS_FILE.write_text(json.dumps({"images_dir": str(images_dir)}))


IMAGES_DIR = _persisted_images_dir() or Path(os.environ.get("IMAGES_DIR", "./images"))

# RAM++ (Recognize Anything Model) — automatic open-set tagger, no vocabulary needed.
# Its checkpoint ships with per-tag tuned thresholds, so RAM_CONFIDENCE defaults to
# None ("use the model's own thresholds") rather than a single guessed global number;
# set it only to override every tag's threshold uniformly.
RAM_CHECKPOINT_PATH = Path(os.environ.get("RAM_CHECKPOINT_PATH", "pretrained/ram_plus_swin_large_14m.pth"))
RAM_IMAGE_SIZE = int(os.environ.get("RAM_IMAGE_SIZE", "384"))
_ram_confidence_env = os.environ.get("RAM_CONFIDENCE")
RAM_CONFIDENCE = float(_ram_confidence_env) if _ram_confidence_env else None

# Extra words to look for on top of RAM++'s automatic tags — matched via CLIP
# image/text cosine similarity (the same CLIP model used for "Find Similar"
# in embeddings.py), not RAM++'s own open-set mode (which needs a separate
# CLIP package and replaces the whole tag vocabulary rather than adding to it).
RAM_CUSTOM_TAGS: list[str] = []
RAM_CUSTOM_TAG_THRESHOLD = float(os.environ.get("RAM_CUSTOM_TAG_THRESHOLD", "0.22"))

# Optional reference images for a custom tag, for named entities/characters CLIP's
# bare text embedding alone may not pin down well (e.g. "zeus"): drop a few example
# photos in RAM_CUSTOM_TAG_REFERENCE_DIR/<tag name>/ and they're blended in with the
# text embedding to build a more accurate match target for that tag.
RAM_CUSTOM_TAG_REFERENCE_DIR = Path(os.environ.get("RAM_CUSTOM_TAG_REFERENCE_DIR", "reference_tags"))

# How many dominant-color clusters to look for per image, and how big a
# share of the image a color must cover to be reported. Busy images (many
# small UI elements/icons of different colors) benefit from a higher
# COLOR_CLUSTERS and/or a lower COLOR_MIN_SHARE so smaller color regions
# (e.g. a green accent) don't get merged away or filtered out.
COLOR_CLUSTERS = int(os.environ.get("COLOR_CLUSTERS", "4"))
COLOR_MIN_SHARE = float(os.environ.get("COLOR_MIN_SHARE", "0.08"))
