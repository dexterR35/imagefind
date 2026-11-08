import ctypes
import ctypes.util
import datetime
import os
import struct
import sys
from pathlib import Path

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


# statx(2) constants and struct offsets (linux/stat.h). stx_btime is the second
# statx_timestamp after stx_atime: {s64 tv_sec; u32 tv_nsec; s32 reserved}.
_AT_FDCWD = -100
_STATX_BTIME = 0x800
_STATX_BUFFER_SIZE = 256
_STATX_BTIME_OFFSET = 80
_libc = None


def _linux_birth_time(path: Path) -> float | None:
    """File creation time via glibc statx(), which os.stat() does not expose
    on Linux. None when libc lacks statx or the filesystem does not record a
    birth time (older NFS, some FUSE mounts); ext4/btrfs/xfs and CIFS/SMB
    shares do."""
    global _libc
    try:
        if _libc is None:
            _libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
        statx = _libc.statx
    except (OSError, AttributeError):
        return None
    buffer = ctypes.create_string_buffer(_STATX_BUFFER_SIZE)
    if statx(_AT_FDCWD, os.fsencode(path), 0, _STATX_BTIME, buffer) != 0:
        return None
    mask = struct.unpack_from("I", buffer, 0)[0]
    if not mask & _STATX_BTIME:
        return None
    seconds, nanoseconds = struct.unpack_from("qI", buffer, _STATX_BTIME_OFFSET)
    return seconds + nanoseconds / 1e9 if seconds > 0 else None


def file_added_time(path: Path, stat: os.stat_result) -> float:
    """When the file appeared on this filesystem/NAS share - its creation
    (birth) time. A Windows/SMB copy keeps the original mtime, so mtime alone
    would file a picture copied in today under the day it was first saved.

    st_ctime is deliberately never used on POSIX: there it is the inode
    *change* time, bumped by any chmod, chown or rename, so a single
    `chmod -R` would stamp the whole library with the same "added" date.
    Without a birth time, mtime is the best remaining approximation.
    """
    birth = getattr(stat, "st_birthtime", None)  # macOS/BSD, Windows (3.12+)
    if birth is None and sys.platform.startswith("linux"):
        birth = _linux_birth_time(path)
    if birth is None and os.name == "nt":
        birth = stat.st_ctime  # creation time on Windows before Python 3.12
    return float(birth) if birth else stat.st_mtime


def flatten_to_rgb(image: Image.Image) -> Image.Image:
    """Composite a transparent image onto a white background and return a plain
    RGB image (matches how it renders in the UI); images with no alpha channel
    are just converted to RGB. Shared by every model input path — thumbnails,
    image embeddings, RAM++ tagging, reference-tag matching — so they all agree
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
