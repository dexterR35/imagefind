"""Find *where* a word appears in one image, as boxes.

On demand only (a few seconds per image on CPU), never during indexing.
OWLv2 does the open-vocabulary detection for the word itself. For a custom
tag with reference pictures it also proposes likely-object boxes, and each
crop is compared with the references using the search embedding model. That
finds a small instance on a busy sheet, which a whole-image comparison
can't. OWLv2's own image-guided mode was tried and returned large, confidently
wrong boxes on icon sheets.
"""
import threading

import numpy as np
import torch
from PIL import Image
from torchvision.ops import box_convert, nms

from . import config, embeddings

_device = "cuda" if torch.cuda.is_available() else "cpu"
_model = None
_processor = None
_lock = threading.Lock()
# Overlapping boxes for one object are merged above this IoU.
_TEXT_NMS_IOU = 0.3
_PROPOSAL_NMS_IOU = 0.5
_SAME_OBJECT_IOU = 0.5
# Share of the smaller box inside the larger one that makes it a part.
_PART_OF_OBJECT = 0.7
# Likely-object boxes compared against reference pictures, best first.
_MAX_PROPOSALS = 24
_MIN_CROP_PX = 16


def _load():
    global _model, _processor
    if _model is None:
        from transformers import Owlv2ForObjectDetection, Owlv2Processor

        _processor = Owlv2Processor.from_pretrained(config.DETECTOR_MODEL)
        _model = Owlv2ForObjectDetection.from_pretrained(config.DETECTOR_MODEL).to(_device).eval()
    return _model, _processor


def _normalized(box: list[float], width: int, height: int) -> list[float]:
    return [
        round(box[0] / width, 4), round(box[1] / height, 4),
        round(box[2] / width, 4), round(box[3] / height, 4),
    ]


def _detect(image: Image.Image, word: str):
    """One OWLv2 pass: (boxes in image pixels, per-box score for `word`,
    per-box objectness). The boxes are the same whatever the word is."""
    width, height = image.size
    with _lock:
        model, processor = _load()
        inputs = processor(text=[[word]], images=image, return_tensors="pt").to(_device)
        with torch.no_grad():
            outputs = model(**inputs)
    # OWLv2 pads the image to a square at the bottom/right before predicting,
    # so its normalized boxes are fractions of the longer side.
    side = max(width, height)
    boxes = box_convert(outputs.pred_boxes[0].cpu(), "cxcywh", "xyxy") * side
    boxes[:, 0::2] = boxes[:, 0::2].clamp(0, width)
    boxes[:, 1::2] = boxes[:, 1::2].clamp(0, height)
    return boxes, outputs.logits[0, :, 0].sigmoid().cpu(), outputs.objectness_logits[0].sigmoid().cpu()


def _proposals(image: Image.Image, boxes: torch.Tensor, objectness: torch.Tensor):
    """The most object-like distinct boxes, and their crops' embeddings."""
    indices = nms(boxes, objectness, _PROPOSAL_NMS_IOU)[:_MAX_PROPOSALS].tolist()
    indices = [
        i for i in indices
        if boxes[i, 2] - boxes[i, 0] >= _MIN_CROP_PX and boxes[i, 3] - boxes[i, 1] >= _MIN_CROP_PX
    ]
    crops = [image.crop(tuple(boxes[i].tolist())) for i in indices]
    return indices, embeddings.embed_images(crops)


def find_in_image(
    image: Image.Image,
    word: str,
    references: list[np.ndarray],
    text_threshold: float | None = None,
    example_threshold: float | None = None,
    max_boxes: int = 10,
) -> list[dict]:
    """Boxes for `word` in an RGB image, best first. Each is
    {"label", "score", "source": "word" | "example", "box": [x0, y0, x1, y1]}
    with coordinates as fractions of the image's width and height."""
    if text_threshold is None:
        text_threshold = config.DETECT_TEXT_THRESHOLD
    if example_threshold is None:
        example_threshold = config.DETECT_EXAMPLE_THRESHOLD
    width, height = image.size
    boxes, text_scores, objectness = _detect(image, word)

    found: list[dict] = []
    candidates = torch.nonzero(text_scores >= text_threshold).flatten()
    if len(candidates):
        kept = candidates[nms(boxes[candidates], text_scores[candidates], _TEXT_NMS_IOU)]
        found += [
            {"label": word, "score": round(float(text_scores[i]), 3), "source": "word",
             "box": _normalized(boxes[i].tolist(), width, height)}
            for i in kept.tolist()
        ]

    if references:
        indices, crop_vectors = _proposals(image, boxes, objectness)
        if indices:
            similarity = (crop_vectors @ np.stack(references).T).max(axis=1)
            matches = [(i, float(s)) for i, s in zip(indices, similarity) if s >= example_threshold]
            if matches:
                index = torch.tensor([i for i, _ in matches])
                scores = torch.tensor([s for _, s in matches])
                for k in nms(boxes[index], scores, _TEXT_NMS_IOU).tolist():
                    found.append({
                        "label": word, "score": round(float(scores[k]), 3), "source": "example",
                        "box": _normalized(boxes[index[k]].tolist(), width, height),
                    })

    return _merge_same_object(found)[:max_boxes]


def tags_found_by_example(
    image: Image.Image,
    references_by_tag: dict[str, list[np.ndarray]],
    example_threshold: float | None = None,
) -> list[str]:
    """Custom tags with an object somewhere in the image that looks like one
    of the tag's reference pictures - catches small instances on a busy sheet
    that whole-image matching misses. One OWLv2 pass covers every tag."""
    if example_threshold is None:
        example_threshold = config.DETECT_EXAMPLE_THRESHOLD
    references_by_tag = {tag: refs for tag, refs in references_by_tag.items() if refs}
    if not references_by_tag:
        return []
    boxes, _, objectness = _detect(image, "object")
    indices, crop_vectors = _proposals(image, boxes, objectness)
    if not indices:
        return []
    return sorted(
        tag for tag, refs in references_by_tag.items()
        if float((crop_vectors @ np.stack(refs).T).max()) >= example_threshold
    )


def _merge_same_object(found: list[dict]) -> list[dict]:
    """Best first, dropping any box that is the same object as a stronger one:
    either mostly overlapping it (the word and a reference picture boxing the
    same thing) or mostly inside it (a part, e.g. a gun's stock and barrel)."""
    kept: list[dict] = []
    for item in sorted(found, key=lambda entry: entry["score"], reverse=True):
        x0, y0, x1, y1 = item["box"]
        area = (x1 - x0) * (y1 - y0)
        duplicate = False
        for other in kept:
            ox0, oy0, ox1, oy1 = other["box"]
            inter = max(0.0, min(x1, ox1) - max(x0, ox0)) * max(0.0, min(y1, oy1) - max(y0, oy0))
            other_area = (ox1 - ox0) * (oy1 - oy0)
            union = area + other_area - inter
            if (union and inter / union >= _SAME_OBJECT_IOU) or (
                min(area, other_area) and inter / min(area, other_area) >= _PART_OF_OBJECT
            ):
                duplicate = True
                break
        if not duplicate:
            kept.append(item)
    return kept
