from PIL import Image, ImageDraw

from app.colors import _hsv_to_name, extract_dominant_colors


def test_hsv_to_name_basic_colors():
    assert _hsv_to_name(0.0, 0.9, 0.9) == "red"
    assert _hsv_to_name(0.33, 0.9, 0.9) == "green"
    assert _hsv_to_name(0.6, 0.9, 0.9) == "blue"
    assert _hsv_to_name(0.0, 0.0, 0.95) == "white"
    assert _hsv_to_name(0.0, 0.0, 0.05) == "black"


def test_extract_dominant_colors_solid_red():
    img = Image.new("RGBA", (30, 30), (220, 20, 20, 255))
    assert extract_dominant_colors(img) == ["red"]


def test_extract_dominant_colors_ignores_transparent_pixels():
    img = Image.new("RGBA", (40, 40), (0, 255, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rectangle([10, 10, 29, 29], fill=(0, 200, 0, 255))
    assert extract_dominant_colors(img) == ["green"]


def test_extract_dominant_colors_finds_multiple_colors():
    img = Image.new("RGBA", (40, 20), (220, 20, 20, 255))
    draw = ImageDraw.Draw(img)
    draw.rectangle([20, 0, 39, 19], fill=(20, 20, 220, 255))
    result = extract_dominant_colors(img)
    assert set(result) == {"red", "blue"}
