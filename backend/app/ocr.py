import threading
from pathlib import Path

import easyocr
import torch

_reader = None
_load_lock = threading.Lock()


def _get_reader():
    global _reader
    if _reader is None:
        with _load_lock:
            if _reader is None:
                _reader = easyocr.Reader(["en"], gpu=torch.cuda.is_available())
    return _reader


def extract_text(image_path: Path) -> str:
    results = _get_reader().readtext(str(image_path), detail=0)
    return " ".join(results).strip()
