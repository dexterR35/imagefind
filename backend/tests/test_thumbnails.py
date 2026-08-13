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


def test_make_thumbnail_flattens_transparent_background_to_white_not_black(tmp_path):
    src = tmp_path / "icon.png"
    img = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    # opaque green square in the middle, fully transparent everywhere else
    for x in range(30, 70):
        for y in range(30, 70):
            img.putpixel((x, y), (30, 180, 30, 255))
    img.save(src)
    dest = tmp_path / "thumb.jpg"

    make_thumbnail(src, dest, max_size=100)

    with Image.open(dest) as thumb:
        assert thumb.mode == "RGB"
        corner = thumb.getpixel((2, 2))
        assert min(corner) > 200, f"expected a white-ish corner, got {corner}"
