import hashlib
import hmac
import logging
import os
import threading
from dataclasses import dataclass, field

import requests

from . import config

logger = logging.getLogger(__name__)

# "resolve" (not "blob") gives the raw file bytes directly - the blob URL
# objects.py points users at for manual download is an HTML viewer page.
RAM_CHECKPOINT_REVISION = "6afd703f04b23e1c18d4c7d9882c4f5f954848f8"
RAM_CHECKPOINT_SHA256 = "497c178836ba66698ca226c7895317e6e800034be986452dbd2593298d50e87d"
RAM_CHECKPOINT_SIZE = 3_010_210_801
RAM_CHECKPOINT_DOWNLOAD_URL = (
    "https://huggingface.co/xinyu1205/recognize-anything-plus-model/resolve/"
    f"{RAM_CHECKPOINT_REVISION}/ram_plus_swin_large_14m.pth"
)

_CHUNK_SIZE = 1024 * 1024
_verification_lock = threading.Lock()
_verified_signature: tuple[str, int, int] | None = None


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


def _checkpoint_signature(path) -> tuple[str, int, int] | None:
    try:
        stat = path.stat()
        return str(path.resolve()), stat.st_size, stat.st_mtime_ns
    except OSError:
        return None


def verify_ram_checkpoint() -> bool:
    """Verify the publisher-pinned checkpoint before any pickle-based load.

    A success is cached only for the exact path/size/mtime tuple. Status
    polling therefore does not repeatedly hash 3 GB, while replacing the file
    automatically invalidates the cache.
    """
    global _verified_signature
    path = config.RAM_CHECKPOINT_PATH
    signature = _checkpoint_signature(path)
    if signature is None or signature[1] != RAM_CHECKPOINT_SIZE:
        return False
    with _verification_lock:
        if signature == _verified_signature:
            return True
        digest = hashlib.sha256()
        try:
            with path.open("rb") as checkpoint:
                for chunk in iter(lambda: checkpoint.read(_CHUNK_SIZE), b""):
                    digest.update(chunk)
        except OSError:
            return False
        if not hmac.compare_digest(digest.hexdigest(), RAM_CHECKPOINT_SHA256):
            return False
        _verified_signature = signature
        return True


def is_ram_checkpoint_installed() -> bool:
    return verify_ram_checkpoint()


def run_download(job: ModelDownloadJob) -> None:
    """Streams the checkpoint to a .part file next to the real destination
    and only renames it into place once the download finishes cleanly - so a
    crash, cancel, or network failure never leaves a truncated file where
    objects.py's is_file() check would mistake it for a real install."""
    dest = config.RAM_CHECKPOINT_PATH
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        with requests.get(RAM_CHECKPOINT_DOWNLOAD_URL, stream=True, timeout=(10, 120)) as resp:
            resp.raise_for_status()
            job.total_bytes = int(resp.headers.get("content-length", 0))
            with open(tmp, "wb") as f:
                for chunk in resp.iter_content(chunk_size=_CHUNK_SIZE):
                    if job.cancel_event.is_set():
                        job.cancelled = True
                        break
                    if chunk:
                        f.write(chunk)
                        digest.update(chunk)
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
            if job.downloaded_bytes != RAM_CHECKPOINT_SIZE:
                raise IOError(
                    f"unexpected checkpoint size: received {job.downloaded_bytes} bytes; "
                    f"expected {RAM_CHECKPOINT_SIZE}"
                )
            actual_sha256 = digest.hexdigest()
            if not hmac.compare_digest(actual_sha256, RAM_CHECKPOINT_SHA256):
                raise IOError(
                    "checkpoint SHA-256 mismatch: "
                    f"received {actual_sha256}; expected {RAM_CHECKPOINT_SHA256}"
                )
            os.replace(tmp, dest)
    except Exception as exc:
        tmp.unlink(missing_ok=True)
        job.error = f"Failed to download RAM++ checkpoint: {exc}"
        logger.warning("model download failed: %s", exc)
    finally:
        job.done = True
