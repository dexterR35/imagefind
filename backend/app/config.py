import os
from pathlib import Path

IMAGES_DIR = Path(os.environ.get("IMAGES_DIR", "./images"))
INDEX_DIR = Path(os.environ.get("INDEX_DIR", "./.index"))
VOCABULARY = [
    "clover", "horseshoe", "pot of gold", "coin", "dice",
    "hat", "logo", "trophy", "rainbow", "diamond",
]

# Model tuning — lower a confidence value to catch more (but noisier) matches,
# raise it to catch fewer (but more certain) ones. Illustrated/vector art
# (icons, logos) commonly scores lower than real photos on these models, so
# if searches for "person" or an object come back empty, try lowering the
# matching *_CONFIDENCE value before assuming nothing was detected.
YOLO_CONFIDENCE = float(os.environ.get("YOLO_CONFIDENCE", "0.4"))
OWL_CONFIDENCE = float(os.environ.get("OWL_CONFIDENCE", "0.15"))
TEXT_SIMILARITY_THRESHOLD = float(os.environ.get("TEXT_SIMILARITY_THRESHOLD", "0.2"))

# How many dominant-color clusters to look for per image, and how big a
# share of the image a color must cover to be reported. Busy images (many
# small UI elements/icons of different colors) benefit from a higher
# COLOR_CLUSTERS and/or a lower COLOR_MIN_SHARE so smaller color regions
# (e.g. a green accent) don't get merged away or filtered out.
COLOR_CLUSTERS = int(os.environ.get("COLOR_CLUSTERS", "4"))
COLOR_MIN_SHARE = float(os.environ.get("COLOR_MIN_SHARE", "0.08"))
