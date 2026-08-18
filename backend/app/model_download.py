import logging
import os
import threading
from dataclasses import dataclass, field

import requests

from . import config

logger = logging.getLogger(__name__)

# "resolve" (not "blob") gives the raw file bytes directly - the blob URL
# objects.py points users at for manual download is an HTML viewer page.
RAM_CHECKPOINT_DOWNLOAD_URL = (
    "https://huggingface.co/xinyu1205/recognize-anything-plus-model/resolve/main/ram_plus_swin_large_14m.pth"
)

_CHUNK_SIZE = 1024 * 1024


@dataclass
class ModelDownloadJob:
    id: str
    downloaded_bytes: int = 0
    total_bytes: int = 0
    done: bool = False
    error: str | None = None
    cancelled: bool = False
    # Not exposed to API callers directly - set via a cancel endpoint the
    # same way ReindexJob.cancel_event is.
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False, compare=False)


def is_ram_checkpoint_installed() -> bool:
    return config.RAM_CHECKPOINT_PATH.is_file()


def run_download(job: ModelDownloadJob) -> None:
    """Streams the checkpoint to a .part file next to the real destination
    and only renames it into place once the download finishes cleanly - so a
    crash, cancel, or network failure never leaves a truncated file where
    objects.py's is_file() check would mistake it for a real install."""
    dest = config.RAM_CHECKPOINT_PATH
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with requests.get(RAM_CHECKPOINT_DOWNLOAD_URL, stream=True, timeout=30) as resp:
            resp.raise_for_status()
            job.total_bytes = int(resp.headers.get("content-length", 0))
            with open(tmp, "wb") as f:
                for chunk in resp.iter_content(chunk_size=_CHUNK_SIZE):
                    if job.cancel_event.is_set():
                        job.cancelled = True
                        break
                    if chunk:
                        f.write(chunk)
                        job.downloaded_bytes += len(chunk)
        # A cancel request may arrive after the final chunk but before the
        # atomic rename. Honour it here as well so the endpoint never reports
        # cancelled while a checkpoint was installed anyway.
        if job.cancel_event.is_set():
            job.cancelled = True
        if job.cancelled:
            tmp.unlink(missing_ok=True)
        else:
            if job.total_bytes and job.downloaded_bytes != job.total_bytes:
                raise IOError(
                    "incomplete download: "
                    f"received {job.downloaded_bytes} of {job.total_bytes} bytes"
                )
            os.replace(tmp, dest)
    except Exception as exc:
        tmp.unlink(missing_ok=True)
        job.error = f"Failed to download RAM++ checkpoint: {exc}"
        logger.warning("model download failed: %s", exc)
    finally:
        job.done = True
