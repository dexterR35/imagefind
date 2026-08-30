import threading
from pathlib import Path

import easyocr
import numpy as np
import torch
from PIL import Image, ImageOps

_reader = None
_load_lock = threading.Lock()


def _get_reader():
    global _reader
    if _reader is None:
        with _load_lock:
            if _reader is None:
                _reader = easyocr.Reader(["en"], gpu=torch.cuda.is_available())
    return _reader


def extract_text(image_path: Path, *, image: Image.Image | None = None) -> str:
    # EasyOCR passes string paths to cv2.imread(), which cannot reliably open
    # Unicode filenames on Windows (for example names containing ™ or an en
    # dash). Decode through Pillow first and give EasyOCR the pixel data. A
    # grayscale array is sufficient for OCR and also avoids OpenCV/libpng
    # warnings caused by malformed embedded PNG colour profiles.
    #
    # `image`, when supplied, is the already-opened, EXIF-corrected, display-
    # ready RGB image the indexer decoded once for the whole pipeline, so the
    # original file is not re-read from disk here.
    if image is not None:
        grayscale = np.asarray(image.convert("L")).copy()
    else:
        with Image.open(image_path) as opened:
            # Honour EXIF orientation so rotated phone photos are read the right
            # way up, matching how the thumbnail and CLIP/RAM++ inputs see them.
            opened = ImageOps.exif_transpose(opened) or opened
            grayscale = np.asarray(opened.convert("L")).copy()
    results = _get_reader().readtext(grayscale, detail=0)
    return " ".join(results).strip()
