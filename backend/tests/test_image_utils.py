from PIL import Image

from app.image_utils import flatten_to_rgb


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
