from PIL import Image

from app.objects import detect_all_objects


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
