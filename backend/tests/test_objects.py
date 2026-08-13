from PIL import Image

from app.objects import detect_all_objects, detect_vocab_objects


def test_detect_all_objects_returns_deduped_sorted_list(tmp_path):
    # A synthetic blank image won't reliably trigger real detections from either
    # model; this test verifies the pipeline runs end-to-end without erroring and
    # returns the right contract (list[str], deduplicated, sorted).
    img = Image.new("RGB", (416, 416), (200, 200, 200))
    path = tmp_path / "blank.png"
    img.save(path)

    result = detect_all_objects(path, vocabulary=["clover", "horseshoe"])

    assert isinstance(result, list)
    assert result == sorted(set(result))


def test_detect_vocab_objects_handles_transparent_rgba_image(tmp_path):
    # Previously `.convert("RGB")` on an RGBA image silently flattened transparent
    # pixels onto black; this just needs to run without erroring on that input and
    # return the right contract now that transparency is composited onto white.
    img = Image.new("RGBA", (416, 416), (0, 0, 0, 0))
    path = tmp_path / "transparent.png"
    img.save(path)

    result = detect_vocab_objects(path, vocabulary=["clover", "horseshoe"])

    assert isinstance(result, list)
    assert result == sorted(set(result))
