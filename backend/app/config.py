import json
import os
from pathlib import Path

INDEX_DIR = Path(os.environ.get("INDEX_DIR", "./.index"))
SETTINGS_FILE = INDEX_DIR / "settings.json"


def _load_persisted_settings() -> dict:
    try:
        return json.loads(SETTINGS_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


_persisted = _load_persisted_settings()


def save_settings(images_dir: Path, ram_confidence: float | None, ram_custom_tags: list[str]) -> None:
    """Overwrites the whole settings file with the current live values every
    call (rather than merging in just the field that changed) so it always
    mirrors _settings_dict() exactly - the same source of truth main.py's
    GET /settings reads from in memory."""
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({
        "images_dir": str(images_dir),
        "ram_confidence": ram_confidence,
        "ram_custom_tags": ram_custom_tags,
    }))
    os.replace(tmp, SETTINGS_FILE)


IMAGES_DIR = (
    Path(_persisted["images_dir"]) if _persisted.get("images_dir")
    else Path(os.environ.get("IMAGES_DIR", "./images"))
)

# Which browser origins may call this API. Defaults to the Vite dev server
# only; set to the server machine's actual LAN address(es) — comma-separated
# — once other users on the network need to reach it (e.g.
# "http://192.168.1.50:5173,http://192.168.1.50:3000").
CORS_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("CORS_ALLOWED_ORIGINS", "http://localhost:5173").split(",")
    if origin.strip()
]

# RAM++ (Recognize Anything Model) — automatic open-set tagger, no vocabulary needed.
# Its checkpoint ships with per-tag tuned thresholds, so RAM_CONFIDENCE defaults to
# None ("use the model's own thresholds") rather than a single guessed global number;
# set it only to override every tag's threshold uniformly.
RAM_CHECKPOINT_PATH = Path(os.environ.get("RAM_CHECKPOINT_PATH", "pretrained/ram_plus_swin_large_14m.pth"))
RAM_IMAGE_SIZE = int(os.environ.get("RAM_IMAGE_SIZE", "384"))
if "ram_confidence" in _persisted:
    # A persisted explicit null must stay None, not fall through to the env
    # var default - same "explicit null is meaningful" rule as main.py's
    # SettingsUpdate uses for the live /settings endpoint.
    RAM_CONFIDENCE = _persisted["ram_confidence"]
else:
    _ram_confidence_env = os.environ.get("RAM_CONFIDENCE")
    RAM_CONFIDENCE = float(_ram_confidence_env) if _ram_confidence_env else None

# Extra words to look for on top of RAM++'s automatic tags — matched via CLIP
# image/text cosine similarity (the same CLIP model used for "Find Similar"
# in embeddings.py), not RAM++'s own open-set mode (which needs a separate
# CLIP package and replaces the whole tag vocabulary rather than adding to it).
RAM_CUSTOM_TAGS: list[str] = _persisted.get("ram_custom_tags", [])
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

# DAM-style real-time indexing: a watchdog observer processes new/changed
# files as they land instead of waiting for a manual reindex. Off by default
# so tests (which reload config/main repeatedly) never spin up real
# background threads/OS file watchers; set true in the actual deployment.
ENABLE_WATCHER = os.environ.get("ENABLE_WATCHER", "false").lower() == "true"
# How long a file's size must stay unchanged across polls before the watcher
# treats a create/modify event as "the write is finished" and processes it —
# guards against reading a file mid-copy over the NAS.
WATCHER_STABLE_CHECK_SECONDS = float(os.environ.get("WATCHER_STABLE_CHECK_SECONDS", "1.0"))
# Backstop for the real-time watcher: catches anything a missed file-system
# event didn't (e.g. SMB change-notifications aren't 100% guaranteed for
# writes from other machines onto the same NAS share). This is a full
# needs_reindex()-gated scan, same as the manual Reindex button, just run on
# a low-frequency timer instead of only on click.
RECONCILE_INTERVAL_SECONDS = float(os.environ.get("RECONCILE_INTERVAL_SECONDS", str(4 * 60 * 60)))
