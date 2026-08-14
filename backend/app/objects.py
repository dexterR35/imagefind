import threading
from pathlib import Path

import torch
from PIL import Image
from transformers import Owlv2ForObjectDetection, Owlv2Processor
from ultralytics import YOLO

from . import config

_device = "cuda" if torch.cuda.is_available() else "cpu"
_yolo_model = None
_owl_processor = None
_owl_model = None
_load_lock = threading.Lock()


def _get_yolo():
    global _yolo_model
    if _yolo_model is None:
        # Double-checked locking: the reindex background thread and a
        # /search-triggered request thread can both race to lazy-load the
        # model on first use, so the actual load must happen under a lock,
        # with the outer unlocked check kept only as a fast path afterward.
        with _load_lock:
            if _yolo_model is None:
                _yolo_model = YOLO("yolov8n.pt")
    return _yolo_model


def detect_yolo_objects(image_path: Path, conf: float | None = None) -> list[str]:
    if conf is None:
        conf = config.YOLO_CONFIDENCE
    model = _get_yolo()
    results = model.predict(source=str(image_path), conf=conf, device=_device, verbose=False)
    labels = set()
    for r in results:
        for c in r.boxes.cls.tolist():
            labels.add(model.names[int(c)])
    return sorted(labels)


def _get_owl():
    global _owl_processor, _owl_model
    if _owl_model is None:
        with _load_lock:
            if _owl_model is None:
                _owl_processor = Owlv2Processor.from_pretrained("google/owlv2-base-patch16-ensemble")
                _owl_model = (
                    Owlv2ForObjectDetection.from_pretrained("google/owlv2-base-patch16-ensemble")
                    .to(_device)
                    .eval()
                )
    return _owl_processor, _owl_model


def detect_vocab_objects(image_path: Path, vocabulary: list[str], conf: float | None = None) -> list[str]:
    # conf defaults to None (read from config inside the function) rather than
    # config.OWL_CONFIDENCE directly, because a default argument value is
    # evaluated once at import time — it would freeze in the startup value
    # and never see runtime settings changes made via POST /settings.
    if conf is None:
        conf = config.OWL_CONFIDENCE
    if not vocabulary:
        return []
    processor, model = _get_owl()
    with Image.open(image_path) as raw:
        if raw.mode in ("RGBA", "LA", "P"):
            rgba = raw.convert("RGBA")
            bg = Image.new("RGB", rgba.size, (255, 255, 255))
            bg.paste(rgba, mask=rgba.getchannel("A"))
            image = bg
        else:
            image = raw.convert("RGB")
    inputs = processor(text=[vocabulary], images=image, return_tensors="pt").to(_device)
    with torch.no_grad():
        outputs = model(**inputs)
    target_sizes = torch.tensor([image.size[::-1]])
    results = processor.post_process_object_detection(
        outputs=outputs, threshold=conf, target_sizes=target_sizes
    )[0]
    labels = {vocabulary[i] for i in results["labels"].tolist()}
    return sorted(labels)


def detect_all_objects(
    image_path: Path,
    vocabulary: list[str],
    yolo_conf: float | None = None,
    owl_conf: float | None = None,
) -> list[str]:
    found = (
        set(detect_yolo_objects(image_path, conf=yolo_conf))
        | set(detect_vocab_objects(image_path, vocabulary, conf=owl_conf))
    )
    return sorted(found)
