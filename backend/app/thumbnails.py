from pathlib import Path

from PIL import Image

from .image_utils import flatten_to_rgb


def make_thumbnail(src_path: Path, dest_path: Path, max_size: int = 320) -> None:
    dest_path = Path(dest_path)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(src_path) as img:
        img = flatten_to_rgb(img)
        img.thumbnail((max_size, max_size))
        img.save(dest_path, format="JPEG", quality=85)
