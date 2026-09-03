from pathlib import Path

from PIL import Image

from .image_utils import flatten_to_rgb


def make_thumbnail(
    src_path: Path,
    dest_path: Path,
    max_size: int = 320,
    *,
    image: Image.Image | None = None,
) -> None:
    """Write a <=max_size JPEG preview.

    `image`, when supplied, is an already-opened, display-ready RGB image (see
    flatten_to_rgb) that the indexer decoded once and shares across the whole
    pipeline - it saves re-reading the original file from disk here. It is
    copied before .thumbnail() because that call resizes in place and the
    caller keeps using the same object for embedding and tagging.
    """
    dest_path = Path(dest_path)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    if image is not None:
        img = (image if image.mode == "RGB" else flatten_to_rgb(image)).copy()
        img.thumbnail((max_size, max_size))
        img.save(dest_path, format="JPEG", quality=85)
        return
    with Image.open(src_path) as opened:
        img = flatten_to_rgb(opened)
        img.thumbnail((max_size, max_size))
        img.save(dest_path, format="JPEG", quality=85)
