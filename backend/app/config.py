import os
from pathlib import Path

IMAGES_DIR = Path(os.environ.get("IMAGES_DIR", "./images"))
INDEX_DIR = Path(os.environ.get("INDEX_DIR", "./.index"))
VOCABULARY = [
    "clover", "horseshoe", "pot of gold", "coin", "dice",
    "hat", "logo", "trophy", "rainbow",
]

# Model tuning — lower a confidence value to catch more (but noisier) matches,
# raise it to catch fewer (but more certain) ones. Illustrated/vector art
# (icons, logos) commonly scores lower than real photos on these models, so
# if searches for "person" or an object come back empty, try lowering the
# matching *_CONFIDENCE value before assuming nothing was detected.
YOLO_CONFIDENCE = float(os.environ.get("YOLO_CONFIDENCE", "0.4"))
OWL_CONFIDENCE = float(os.environ.get("OWL_CONFIDENCE", "0.15"))
TEXT_SIMILARITY_THRESHOLD = float(os.environ.get("TEXT_SIMILARITY_THRESHOLD", "0.2"))
