import os
from pathlib import Path

IMAGES_DIR = Path(os.environ.get("IMAGES_DIR", "./images"))
INDEX_DIR = Path(os.environ.get("INDEX_DIR", "./.index"))
VOCABULARY = [
    "clover", "horseshoe", "pot of gold", "coin", "dice",
    "hat", "logo", "trophy", "rainbow",
]
