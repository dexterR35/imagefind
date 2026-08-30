from PIL import Image

import datetime

from app.image_utils import extract_date_taken, flatten_to_rgb


def test_flatten_to_rgb_composites_transparency_onto_white():
    img = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
    result = flatten_to_rgb(img)

    assert result.mode == "RGB"
    assert result.getpixel((5, 5)) == (255, 255, 255)


def test_flatten_to_rgb_leaves_opaque_pixels_untouched():
    img = Image.new("RGBA", (10, 10), (30, 180, 30, 255))
    result = flatten_to_rgb(img)

    assert result.getpixel((5, 5)) == (30, 180, 30)


def test_flatten_to_rgb_converts_plain_rgb_without_change():
    img = Image.new("RGB", (10, 10), (10, 20, 30))
    result = flatten_to_rgb(img)

    assert result.mode == "RGB"
    assert result.getpixel((5, 5)) == (10, 20, 30)


def test_flatten_to_rgb_applies_exif_orientation():
    # A 20x10 landscape image tagged orientation=6 ("rotate 90 CW") must come
    # back as a 10x20 portrait, matching how it renders in a browser.
    img = Image.new("RGB", (20, 10), (10, 20, 30))
    exif = img.getexif()
    exif[274] = 6
    img.info["exif"] = exif.tobytes()

    result = flatten_to_rgb(img)

    assert result.size == (10, 20)


def test_extract_date_taken_prefers_original_capture_date():
    img = Image.new("RGB", (1, 1))
    exif = img.getexif()
    exif[36867] = "2024:05:06 07:08:09"
    exif[306] = "2025:01:02 03:04:05"

    expected = datetime.datetime(2024, 5, 6, 7, 8, 9).timestamp()
    assert extract_date_taken(img, fallback=123.0) == expected


def test_extract_date_taken_uses_next_valid_tag_then_file_mtime():
    img = Image.new("RGB", (1, 1))
    exif = img.getexif()
    exif[36867] = "not-a-date"
    exif[306] = "2025:01:02 03:04:05"
    expected = datetime.datetime(2025, 1, 2, 3, 4, 5).timestamp()
    assert extract_date_taken(img, fallback=123.0) == expected

    empty = Image.new("RGB", (1, 1))
    assert extract_date_taken(empty, fallback=123.0) == 123.0
