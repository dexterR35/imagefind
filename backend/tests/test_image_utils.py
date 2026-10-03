from PIL import Image

import datetime
import os
import sys

import pytest

from app import image_utils
from app.image_utils import extract_date_taken, file_added_time, flatten_to_rgb


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


def test_file_added_time_ignores_posix_ctime(tmp_path, monkeypatch):
    # On Linux st_ctime is the last chmod/rename, never creation time.
    path = tmp_path / "a.png"
    path.write_bytes(b"x")
    os.utime(path, (1_000_000, 1_000_000))
    os.chmod(path, 0o644)  # bumps ctime to now
    monkeypatch.setattr(image_utils, "_linux_birth_time", lambda p: None)
    stat = path.stat()
    if hasattr(stat, "st_birthtime") or os.name == "nt":
        pytest.skip("platform exposes a real creation time")
    assert file_added_time(path, stat) == 1_000_000


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="statx is Linux-only")
def test_linux_birth_time_is_creation_not_mtime(tmp_path):
    path = tmp_path / "a.png"
    path.write_bytes(b"x")
    os.utime(path, (1_000_000, 1_000_000))  # pretend it was copied with an old mtime
    birth = image_utils._linux_birth_time(path)
    if birth is None:
        pytest.skip("filesystem does not record birth time")
    assert birth > 1_000_000
    assert file_added_time(path, path.stat()) == birth
