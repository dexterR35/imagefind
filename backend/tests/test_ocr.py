import numpy as np
from PIL import Image, ImageDraw, ImageFont

from app import ocr
from app.ocr import extract_text


def test_extract_text_reads_rendered_word(tmp_path):
    img = Image.new("RGB", (500, 180), "white")
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=64)
    draw.text((20, 50), "NETBET", fill="black", font=font)
    path = tmp_path / "text.png"
    img.save(path)

    result = extract_text(path)

    assert "NETBET" in result.upper()


def test_extract_text_decodes_unicode_path_before_easyocr(tmp_path, monkeypatch):
    path = tmp_path / "Promo ™ – 550x337.png"
    Image.new("RGB", (32, 16), "white").save(path)

    class FakeReader:
        def readtext(self, image, detail):
            assert isinstance(image, np.ndarray)
            assert image.shape == (16, 32)
            assert image.dtype == np.uint8
            assert detail == 0
            return ["PROMO"]

    monkeypatch.setattr(ocr, "_get_reader", lambda: FakeReader())

    assert extract_text(path) == "PROMO"
