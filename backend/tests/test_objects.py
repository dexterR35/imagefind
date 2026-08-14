import numpy as np
import torch
from PIL import Image

from app import objects as objects_mod
from app.embeddings import cosine_similarity, embed_image, embed_text
from app.objects import detect_custom_tags, detect_ram_objects


def test_detect_ram_objects_returns_deduped_sorted_list(tmp_path):
    # A synthetic blank image won't reliably trigger real detections; this
    # test verifies the pipeline runs end-to-end without erroring and
    # returns the right contract (list[str], deduplicated, sorted).
    img = Image.new("RGB", (416, 416), (200, 200, 200))
    path = tmp_path / "blank.png"
    img.save(path)

    result = detect_ram_objects(path)

    assert isinstance(result, list)
    assert result == sorted(set(result))


def test_detect_custom_tags_matches_a_visually_obvious_tag():
    red_img = Image.new("RGB", (224, 224), (220, 20, 20))
    image_embedding = embed_image(red_img)

    # CLIP's absolute cosine similarities vary by phrasing/model version, so rather
    # than assume a fixed magic threshold, derive one from the real, measured gap
    # between the matching and non-matching tag for this exact image/text pair.
    red_sim = cosine_similarity(image_embedding, embed_text("a solid red square"))
    blue_sim = cosine_similarity(image_embedding, embed_text("a solid blue square"))
    assert red_sim > blue_sim
    threshold = (red_sim + blue_sim) / 2

    result = detect_custom_tags(
        image_embedding, ["a solid red square", "a solid blue square"], threshold=threshold
    )

    assert result == ["a solid red square"]


def test_detect_custom_tags_empty_list_returns_empty_without_embedding_calls():
    assert detect_custom_tags(np.zeros(512, dtype=np.float32), [], threshold=0.2) == []


def test_detect_custom_tags_applies_threshold_and_caches_tag_embeddings(monkeypatch):
    calls = []

    def fake_embed_text(tag):
        calls.append(tag)
        return np.array([1.0, 0.0], dtype=np.float32) if tag == "match" else np.array([0.0, 1.0], dtype=np.float32)

    monkeypatch.setattr(objects_mod.embeddings, "embed_text", fake_embed_text)
    objects_mod._tag_embedding_cache.clear()

    image_embedding = np.array([1.0, 0.0], dtype=np.float32)

    result = detect_custom_tags(image_embedding, ["match", "no-match"], threshold=0.5)
    assert result == ["match"]
    assert calls == ["match", "no-match"]

    # Second call with the same tags must not re-embed them (cache hit).
    detect_custom_tags(image_embedding, ["match", "no-match"], threshold=0.5)
    assert calls == ["match", "no-match"]


def test_detect_ram_objects_overrides_class_threshold_only_when_conf_given(tmp_path, monkeypatch):
    img = Image.new("RGB", (416, 416), (200, 200, 200))
    path = tmp_path / "blank.png"
    img.save(path)

    fake_model = type("FakeModel", (), {"class_threshold": torch.zeros(3)})()

    def fake_get_ram():
        return (lambda image: torch.zeros(3, 4, 4)), fake_model

    def fake_inference(tensor, model):
        return "a | b", ""

    monkeypatch.setattr(objects_mod, "_get_ram", fake_get_ram)
    monkeypatch.setattr(objects_mod, "_ram_inference", fake_inference)

    detect_ram_objects(path)
    assert torch.equal(fake_model.class_threshold, torch.zeros(3))

    detect_ram_objects(path, conf=0.7)
    assert torch.equal(fake_model.class_threshold, torch.full((3,), 0.7))


def test_detect_ram_objects_handles_transparent_rgba_image(tmp_path):
    # Previously `.convert("RGB")` on an RGBA image silently flattened transparent
    # pixels onto black; this just needs to run without erroring on that input and
    # return the right contract now that transparency is composited onto white.
    img = Image.new("RGBA", (416, 416), (0, 0, 0, 0))
    path = tmp_path / "transparent.png"
    img.save(path)

    result = detect_ram_objects(path)

    assert isinstance(result, list)
    assert result == sorted(set(result))
