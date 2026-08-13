from PIL import Image

from app.thumbnails import make_thumbnail


def test_make_thumbnail_resizes_and_creates_parent_dirs(tmp_path):
    src = tmp_path / "src.png"
    Image.new("RGB", (800, 600), (10, 20, 30)).save(src)
    dest = tmp_path / "nested" / "thumb.jpg"

    make_thumbnail(src, dest, max_size=320)

    assert dest.exists()
    with Image.open(dest) as thumb:
        assert max(thumb.size) <= 320
        assert thumb.size[0] / thumb.size[1] == 800 / 600
