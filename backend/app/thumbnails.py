from pathlib import Path

from PIL import Image


def make_thumbnail(src_path: Path, dest_path: Path, max_size: int = 320) -> None:
    dest_path = Path(dest_path)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(src_path) as img:
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGBA")
            bg = Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.getchannel("A"))
            img = bg
        else:
            img = img.convert("RGB")
        img.thumbnail((max_size, max_size))
        img.save(dest_path, format="JPEG", quality=85)
