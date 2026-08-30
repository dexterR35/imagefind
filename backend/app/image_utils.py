import datetime

from PIL import Image, ImageOps

_EXIF_DATETIME_TAGS = (
    36867,  # DateTimeOriginal: when the camera captured the image
    36868,  # DateTimeDigitized
    306,    # DateTime: last file/image metadata change
)
_EXIF_DATETIME_FORMAT = "%Y:%m:%d %H:%M:%S"


def extract_date_taken(image: Image.Image, fallback: float) -> float:
    """Best available EXIF capture date as a Unix timestamp.

    Falls back to the file mtime because screenshots and generated images
    commonly carry no EXIF metadata at all.
    """
    try:
        exif = image.getexif()
    except (AttributeError, TypeError, ValueError):
        return fallback

    for tag in _EXIF_DATETIME_TAGS:
        try:
            raw = exif.get(tag)
            if raw:
                value = raw.decode(errors="strict") if isinstance(raw, bytes) else str(raw)
                return datetime.datetime.strptime(value, _EXIF_DATETIME_FORMAT).timestamp()
        except (UnicodeDecodeError, ValueError, TypeError):
            # A malformed higher-priority tag should not prevent a valid
            # lower-priority EXIF date from being used.
            continue
    return fallback


def flatten_to_rgb(image: Image.Image) -> Image.Image:
    """Composite a transparent image onto a white background and return a plain
    RGB image (matches how it renders in the UI); images with no alpha channel
    are just converted to RGB. Shared by every model input path — thumbnails,
    CLIP embeddings, RAM++ tagging, reference-tag matching — so they all agree
    on what a transparent-background image looks like instead of each having
    their own copy of this that could quietly drift apart.

    Any EXIF orientation is baked in first so a phone photo tagged "rotate 90°"
    is thumbnailed, embedded, and tagged the same way it displays, not sideways.
    exif_transpose is a no-op on an image whose orientation tag is missing or 1,
    so calling it again on an already-corrected image is harmless.
    """
    image = ImageOps.exif_transpose(image) or image
    if image.mode in ("RGBA", "LA", "P"):
        rgba = image.convert("RGBA")
        bg = Image.new("RGB", rgba.size, (255, 255, 255))
        bg.paste(rgba, mask=rgba.getchannel("A"))
        return bg
    return image.convert("RGB")
