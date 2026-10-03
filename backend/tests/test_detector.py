from types import SimpleNamespace

import numpy as np
import pytest
import torch
from PIL import Image

from app import detector


class _Inputs(dict):
    """Stands in for the processor's BatchFeature, which moves to a device."""

    def to(self, device):
        return self


def _fake_owl(monkeypatch, pred_boxes, text_logits, objectness_logits):
    outputs = SimpleNamespace(
        pred_boxes=torch.tensor([pred_boxes], dtype=torch.float32),
        logits=torch.tensor([[[v] for v in text_logits]], dtype=torch.float32),
        objectness_logits=torch.tensor([objectness_logits], dtype=torch.float32),
    )
    model = lambda **inputs: outputs  # noqa: E731
    processor = lambda **kwargs: _Inputs()  # noqa: E731
    monkeypatch.setattr(detector, "_load", lambda: (model, processor))


def _logit(p):
    return float(np.log(p / (1 - p)))


def test_boxes_are_mapped_from_the_padded_square_to_image_fractions(monkeypatch):
    # 200x100 image: OWLv2 pads it to 200x200, so a box in the top half of the
    # padded square covers the full height of the real image.
    _fake_owl(
        monkeypatch,
        pred_boxes=[[0.25, 0.25, 0.5, 0.5], [0.26, 0.25, 0.5, 0.5], [0.75, 0.25, 0.5, 0.5]],
        text_logits=[_logit(0.9), _logit(0.6), _logit(0.1)],
        objectness_logits=[0.0, 0.0, 0.0],
    )
    found = detector.find_in_image(Image.new("RGB", (200, 100)), "gun", [], text_threshold=0.25)

    # The near-duplicate second box is merged away; the low-scoring third is dropped.
    assert len(found) == 1
    assert found[0]["label"] == "gun" and found[0]["source"] == "word"
    assert found[0]["score"] == pytest.approx(0.9, abs=1e-3)
    assert found[0]["box"] == [0.0, 0.0, 0.5, 1.0]


def test_reference_pictures_find_boxes_the_word_alone_misses(monkeypatch):
    _fake_owl(
        monkeypatch,
        pred_boxes=[[0.25, 0.25, 0.5, 0.5], [0.75, 0.75, 0.5, 0.5]],
        text_logits=[_logit(0.01), _logit(0.01)],
        objectness_logits=[_logit(0.9), _logit(0.8)],
    )
    reference = np.array([1.0, 0.0], dtype=np.float32)
    # First proposal looks like the reference, second doesn't.
    monkeypatch.setattr(
        detector.embeddings, "embed_images",
        lambda crops: np.array([[0.95, 0.31], [0.2, 0.98]], dtype=np.float32),
    )
    found = detector.find_in_image(
        Image.new("RGB", (100, 100)), "zeus", [reference], example_threshold=0.75
    )

    assert [(f["source"], f["box"]) for f in found] == [("example", [0.0, 0.0, 0.5, 0.5])]
    assert found[0]["score"] == pytest.approx(0.95, abs=1e-3)


def test_word_and_example_boxes_on_the_same_object_are_merged(monkeypatch):
    _fake_owl(
        monkeypatch,
        pred_boxes=[[0.25, 0.25, 0.5, 0.5]],
        text_logits=[_logit(0.4)],
        objectness_logits=[_logit(0.9)],
    )
    monkeypatch.setattr(
        detector.embeddings, "embed_images", lambda crops: np.array([[1.0, 0.0]], dtype=np.float32)
    )
    found = detector.find_in_image(
        Image.new("RGB", (100, 100)), "zeus", [np.array([1.0, 0.0], dtype=np.float32)],
        text_threshold=0.3, example_threshold=0.75,
    )

    assert [(f["source"], f["score"]) for f in found] == [("example", 1.0)]


def test_a_box_mostly_inside_a_stronger_one_is_dropped_as_a_part():
    found = detector._merge_same_object([
        {"label": "gun", "score": 0.3, "source": "word", "box": [0.1, 0.1, 0.3, 0.3]},  # part
        {"label": "gun", "score": 0.45, "source": "word", "box": [0.0, 0.0, 0.5, 0.5]},
        {"label": "gun", "score": 0.35, "source": "word", "box": [0.6, 0.6, 0.9, 0.9]},  # another gun
    ])
    assert [f["score"] for f in found] == [0.45, 0.35]


def test_tags_found_by_example_checks_every_tag_in_one_pass(monkeypatch):
    calls = []
    _fake_owl(
        monkeypatch,
        pred_boxes=[[0.25, 0.25, 0.5, 0.5], [0.75, 0.75, 0.5, 0.5]],
        text_logits=[0.0, 0.0],
        objectness_logits=[_logit(0.9), _logit(0.8)],
    )
    original = detector._detect
    monkeypatch.setattr(detector, "_detect", lambda image, word: calls.append(word) or original(image, word))
    # Crop 1 looks like zeus's reference, crop 2 like nothing.
    monkeypatch.setattr(
        detector.embeddings, "embed_images",
        lambda crops: np.array([[1.0, 0.0, 0.0], [0.0, 0.0, 1.0]], dtype=np.float32),
    )
    found = detector.tags_found_by_example(
        Image.new("RGB", (100, 100)),
        {
            "zeus": [np.array([0.98, 0.2, 0.0], dtype=np.float32)],
            "crown": [np.array([0.0, 1.0, 0.0], dtype=np.float32)],
            "no-examples": [],
        },
        example_threshold=0.75,
    )

    assert found == ["zeus"]
    assert len(calls) == 1


def test_tags_found_by_example_skips_the_detector_without_reference_pictures(monkeypatch):
    monkeypatch.setattr(detector, "_detect", lambda *a: pytest.fail("detector ran"))
    assert detector.tags_found_by_example(Image.new("RGB", (10, 10)), {"zeus": []}) == []
