from PIL import Image, ImageDraw, ImageFont

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
