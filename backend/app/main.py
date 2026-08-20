import logging
import math
import hmac
import json
import threading
import time
import unicodedata
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

SortOption = Literal["date_desc", "date_asc", "name_asc", "name_desc", "size_desc", "size_asc"]

from . import config
from .auth import AuthSession, AuthStore, MAX_PASSWORD_BYTES
from .indexer import Indexer, ReindexJob
from .model_download import ModelDownloadJob, is_ram_checkpoint_installed, run_download
from .rate_limit import SlidingWindowRateLimiter
from .search import find_similar as run_find_similar
from .search import search as run_search
from .storage import IndexStore

logger = logging.getLogger(__name__)
# Uvicorn configures this logger at INFO level, so download activity is
# visible in the same terminal that runs `npm start` / `npm run start:backend`.
activity_logger = logging.getLogger("uvicorn.error")

@asynccontextmanager
async def _lifespan(_app: FastAPI):
    try:
        yield
    finally:
        _stop_realtime_watcher()
        _reconciliation_stop.set()
        if _reconciliation_thread is not None:
            _reconciliation_thread.join(timeout=5)


app = FastAPI(title="ImageFind", lifespan=_lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

store = IndexStore(config.INDEX_DIR)
store.load()
indexer = Indexer(config.IMAGES_DIR, config.INDEX_DIR, store, config.RAM_CUSTOM_TAGS)
jobs: dict[str, ReindexJob] = {}
MAX_JOB_HISTORY = 10
_jobs_lock = threading.Lock()
model_download_jobs: dict[str, ModelDownloadJob] = {}
MAX_MODEL_DOWNLOAD_JOB_HISTORY = 10
_model_download_jobs_lock = threading.Lock()
_settings_lock = threading.Lock()
_catalog_run_lock = threading.Lock()
_search_rate_limiter = SlidingWindowRateLimiter(
    config.SEARCH_RATE_LIMIT_REQUESTS,
    config.SEARCH_RATE_LIMIT_WINDOW_SECONDS,
)
_login_rate_limiter = SlidingWindowRateLimiter(
    config.AUTH_LOGIN_RATE_LIMIT_REQUESTS,
    config.AUTH_LOGIN_RATE_LIMIT_WINDOW_SECONDS,
)
_global_login_rate_limiter = SlidingWindowRateLimiter(
    config.AUTH_GLOBAL_LOGIN_RATE_LIMIT_REQUESTS,
    config.AUTH_GLOBAL_LOGIN_RATE_LIMIT_WINDOW_SECONDS,
)
_login_semaphore = threading.BoundedSemaphore(config.AUTH_MAX_CONCURRENT_LOGINS)
_download_rate_limiter = SlidingWindowRateLimiter(
    config.DOWNLOAD_RATE_LIMIT_REQUESTS,
    config.DOWNLOAD_RATE_LIMIT_WINDOW_SECONDS,
)
auth_store = AuthStore(
    config.AUTH_DB_PATH,
    session_ttl_seconds=config.AUTH_SESSION_TTL_SECONDS,
    max_sessions=config.AUTH_MAX_SESSIONS,
)
SESSION_COOKIE_NAME = "imagefind_session"

_watcher_observer = None
_reconciliation_stop = threading.Event()
_reconciliation_thread = None


def _start_realtime_watcher() -> None:
    global _watcher_observer
    if not config.ENABLE_WATCHER or _watcher_observer is not None:
        return
    from .watcher import start_watcher
    try:
        _watcher_observer = start_watcher(indexer)
    except (OSError, RuntimeError):
        # A NAS can be temporarily unavailable when the server boots. Keep
        # search online and let a later restart/reconfiguration retry instead
        # of crashing the entire API process.
        logger.warning("real-time watcher could not start for %s", indexer.images_dir, exc_info=True)


def _stop_realtime_watcher() -> None:
    global _watcher_observer
    if _watcher_observer is None:
        return
    _watcher_observer.stop()
    _watcher_observer.join(timeout=5)
    _watcher_observer = None


def _no_reindex_running() -> bool:
    with _jobs_lock:
        return not any(not job.done for job in jobs.values())


def _cleanup_orphan_thumbnails() -> None:
    removed = indexer.cleanup_orphan_thumbnails()
    if removed:
        logger.info("removed %d orphan thumbnail(s)", removed)


def _run_manual_reindex(job: ReindexJob, force: bool) -> None:
    with _catalog_run_lock:
        indexer.run_reindex(job, force)


if config.ENABLE_WATCHER:
    from .watcher import start_reconciliation_loop

    _start_realtime_watcher()
    _reconciliation_thread = start_reconciliation_loop(
        indexer,
        lambda: ReindexJob(id=uuid.uuid4().hex),
        config.RECONCILE_INTERVAL_SECONDS,
        _reconciliation_stop,
        can_run=_no_reindex_running,
        run_lock=_catalog_run_lock,
    )
    threading.Thread(
        target=_cleanup_orphan_thumbnails,
        name="imagefind-thumbnail-cleanup",
        daemon=True,
    ).start()


def _entry_to_dict(e) -> dict:
    return {
        "id": e.id, "path": e.path, "thumbnail_url": f"/thumbnail/{e.id}",
        "ocr_text": e.ocr_text, "colors": e.colors, "objects": e.objects,
        "width": e.width, "height": e.height, "format": e.format,
        "size": e.size, "mtime": e.mtime, "date_taken": e.date_taken,
        "indexed_at": e.indexed_at,
    }


def _sanitize_search_value(value: str | None, field: str) -> str | None:
    if value is None:
        return None
    value = unicodedata.normalize("NFC", value).strip()
    if any(unicodedata.category(character) == "Cc" for character in value):
        raise HTTPException(status_code=422, detail=f"{field} contains control characters")
    return value or None


def _enforce_search_rate_limit(request: Request) -> None:
    client_key = _request_rate_limit_key(request)
    retry_after = _search_rate_limiter.retry_after(client_key)
    if retry_after is not None:
        raise HTTPException(
            status_code=429,
            detail="too many search requests",
            headers={"Retry-After": str(max(1, math.ceil(retry_after)))},
        )


def _enforce_download_rate_limit(request: Request) -> None:
    client_key = _request_rate_limit_key(request)
    retry_after = _download_rate_limiter.retry_after(client_key)
    if retry_after is not None:
        raise HTTPException(
            status_code=429,
            detail="too many download requests",
            headers={"Retry-After": str(max(1, math.ceil(retry_after)))},
        )


def _client_ip(request: Request) -> str:
    value = request.headers.get("cf-connecting-ip")
    if not value:
        value = request.client.host if request.client else "unknown"
    return value.split(",", 1)[0].strip().replace("\r", "").replace("\n", "")[:128]


def _request_rate_limit_key(request: Request) -> str:
    session = getattr(request.state, "auth_session", None)
    return f"session:{session.id}" if session is not None else f"ip:{_client_ip(request)}"


def _request_came_through_tunnel(request: Request) -> bool:
    if request.headers.get("cf-ray") or request.headers.get("cf-connecting-ip"):
        return True
    tunnel_markers = (
        request.headers.get("origin", ""),
        request.headers.get("referer", ""),
        request.headers.get("x-forwarded-host", ""),
        request.headers.get("forwarded", ""),
    )
    return any(".trycloudflare.com" in value.lower() for value in tunnel_markers)


def _require_local_admin(request: Request) -> None:
    if _request_came_through_tunnel(request):
        raise HTTPException(
            status_code=403,
            detail="this administration action is available only from the local app",
        )


def _request_is_secure(request: Request) -> bool:
    if request.url.scheme == "https":
        return True
    if request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip() == "https":
        return True
    try:
        return json.loads(request.headers.get("cf-visitor", "{}"))["scheme"] == "https"
    except (KeyError, TypeError, json.JSONDecodeError):
        return False


def _authenticate_request(request: Request) -> AuthSession | None:
    return auth_store.get_session(request.cookies.get(SESSION_COOKIE_NAME))


_PUBLIC_API_PATHS = {"/health", "/auth/login", "/auth/session"}
_PUBLIC_FRONTEND_PATHS = {"/", "/index.html", "/favicon.svg"}
_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _authorize_request(request: Request) -> JSONResponse | None:
    is_public_frontend = (
        request.method in {"GET", "HEAD"}
        and (
            request.url.path in _PUBLIC_FRONTEND_PATHS
            or request.url.path.startswith("/assets/")
        )
    )
    if (
        request.method == "OPTIONS"
        or request.url.path in _PUBLIC_API_PATHS
        or is_public_frontend
    ):
        return None
    session = _authenticate_request(request)
    if session is None:
        return JSONResponse(
            status_code=401,
            content={"detail": "authentication required"},
            headers={"Cache-Control": "no-store", "WWW-Authenticate": "Session"},
        )
    request.state.auth_session = session
    if request.method in _UNSAFE_METHODS:
        supplied = request.headers.get("x-csrf-token", "")
        if not supplied or not hmac.compare_digest(supplied, session.csrf_token):
            return JSONResponse(
                status_code=403,
                content={"detail": "invalid CSRF token"},
                headers={"Cache-Control": "no-store"},
            )
    return None


def _login_rate_limit_denial(request: Request) -> JSONResponse | None:
    global_retry = _global_login_rate_limiter.retry_after("global")
    ip_retry = _login_rate_limiter.retry_after(_client_ip(request))
    retries = [retry for retry in (global_retry, ip_retry) if retry is not None]
    if not retries:
        return None
    return JSONResponse(
        status_code=429,
        content={"detail": "too many login attempts"},
        headers={"Retry-After": str(max(1, math.ceil(max(retries))))},
    )


def _add_security_headers(request: Request, response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; base-uri 'none'; frame-ancestors 'none'; "
        "form-action 'self'; img-src 'self' data: blob:; "
        "style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'",
    )
    response.headers.setdefault(
        "Permissions-Policy",
        "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
    )
    if _request_is_secure(request):
        response.headers.setdefault(
            "Strict-Transport-Security",
            "max-age=31536000; includeSubDomains",
        )
    return response


@app.middleware("http")
async def authentication_middleware(request: Request, call_next):
    if (
        request.method == "POST"
        and request.url.path == "/auth/login"
        and auth_store.is_configured()
    ):
        limited = _login_rate_limit_denial(request)
        if limited is not None:
            return _add_security_headers(request, limited)
    denied = _authorize_request(request)
    if denied is not None:
        return _add_security_headers(request, denied)
    response = await call_next(request)
    return _add_security_headers(request, response)


class LoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_BYTES)


def _session_payload(session: AuthSession) -> dict:
    return {
        "authenticated": True,
        "configured": True,
        "expires_at": session.expires_at,
        "csrf_token": session.csrf_token,
    }


@app.get("/auth/session")
def auth_session_endpoint(request: Request):
    session = _authenticate_request(request)
    if session is not None:
        response = JSONResponse(_session_payload(session))
    else:
        response = JSONResponse({
            "authenticated": False,
            "configured": auth_store.is_configured(),
        })
        if request.cookies.get(SESSION_COOKIE_NAME):
            response.delete_cookie(
                SESSION_COOKIE_NAME,
                path="/",
                secure=_request_is_secure(request),
                httponly=True,
                samesite="strict",
            )
    response.headers["Cache-Control"] = "no-store"
    return response


@app.post("/auth/login")
def auth_login_endpoint(request: Request, login: LoginRequest):
    if not auth_store.is_configured():
        raise HTTPException(
            status_code=503,
            detail="authentication is not configured; run npm run auth:set-password locally",
        )
    client_ip = _client_ip(request)
    if not _login_semaphore.acquire(blocking=False):
        raise HTTPException(
            status_code=429,
            detail="too many login attempts are already being processed",
            headers={"Retry-After": "1"},
        )
    try:
        created = auth_store.create_session(
            login.password,
            client_ip=client_ip,
            user_agent=request.headers.get("user-agent", ""),
        )
    finally:
        _login_semaphore.release()
    if created is None:
        activity_logger.warning("ImageFind login rejected | client=%s", client_ip)
        raise HTTPException(status_code=401, detail="incorrect password")

    token, session = created
    response = JSONResponse(_session_payload(session))
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        max_age=max(0, session.expires_at - int(time.time())),
        path="/",
        secure=_request_is_secure(request),
        httponly=True,
        samesite="strict",
    )
    response.headers["Cache-Control"] = "no-store"
    activity_logger.info("ImageFind login accepted | session=%s | client=%s", session.id, client_ip)
    return response


@app.post("/auth/logout")
def auth_logout_endpoint(request: Request):
    session = request.state.auth_session
    auth_store.delete_session(request.cookies.get(SESSION_COOKIE_NAME))
    response = JSONResponse({"status": "logged out"})
    response.delete_cookie(
        SESSION_COOKIE_NAME,
        path="/",
        secure=_request_is_secure(request),
        httponly=True,
        samesite="strict",
    )
    response.headers["Cache-Control"] = "no-store"
    activity_logger.info("ImageFind logout | session=%s | client=%s", session.id, _client_ip(request))
    return response


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/search")
def search_endpoint(
    request: Request,
    text: str | None = Query(None, max_length=200),
    color: str | None = Query(None, max_length=64),
    object: str | None = Query(None, max_length=128),
    sort: SortOption = "date_desc",
    offset: int = Query(0, ge=0, le=10_000_000),
    limit: int = Query(60, ge=1, le=200),
):
    _enforce_search_rate_limit(request)
    text = _sanitize_search_value(text, "text")
    color = _sanitize_search_value(color, "color")
    object = _sanitize_search_value(object, "object")
    results, total = run_search(
        store, text=text, color=color, obj=object, sort=sort, offset=offset, limit=limit
    )
    return {"results": [_entry_to_dict(e) for e in results], "total": total}


@app.get("/search/similar/{image_id}")
def similar_endpoint(request: Request, image_id: str):
    _enforce_search_rate_limit(request)
    results = run_find_similar(store, image_id)
    if results is None:
        raise HTTPException(status_code=404, detail="image not found")
    return [_entry_to_dict(e) for e in results]


@app.post("/reindex")
def reindex_endpoint(request: Request, force: bool = False):
    _require_local_admin(request)
    with _jobs_lock:
        if any(not job.done for job in jobs.values()):
            raise HTTPException(status_code=409, detail="a reindex job is already running")
        # Evict the oldest completed jobs so `jobs` doesn't grow unbounded on
        # a long-running server where reindex gets triggered repeatedly.
        if len(jobs) >= MAX_JOB_HISTORY:
            for old_id in list(jobs.keys())[: len(jobs) - MAX_JOB_HISTORY + 1]:
                del jobs[old_id]
        job = ReindexJob(id=uuid.uuid4().hex)
        jobs[job.id] = job
    thread = threading.Thread(target=_run_manual_reindex, args=(job, force), daemon=True)
    thread.start()
    return {"job_id": job.id}


@app.get("/reindex/status/{job_id}")
def reindex_status(request: Request, job_id: str):
    _require_local_admin(request)
    with _jobs_lock:
        job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return {
        "processed": job.processed, "total": job.total, "failed": job.failed,
        "done": job.done, "error": job.error, "cancelled": job.cancelled,
    }


@app.post("/reindex/{job_id}/cancel")
def reindex_cancel(request: Request, job_id: str):
    _require_local_admin(request)
    with _jobs_lock:
        job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    if job.done:
        raise HTTPException(status_code=409, detail="job already finished")
    job.cancel_event.set()
    return {"status": "cancelling"}


@app.get("/thumbnail/{image_id}")
def thumbnail_endpoint(image_id: str):
    entry = store.get(image_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="image not found")
    thumbnail = Path(entry.thumbnail_path)
    if not thumbnail.is_file():
        raise HTTPException(status_code=404, detail="thumbnail file not found")
    return FileResponse(thumbnail)


@app.get("/download/{image_id}")
def download_endpoint(request: Request, image_id: str):
    _enforce_download_rate_limit(request)
    entry = store.get(image_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="image not found")
    original = Path(entry.path)
    if not original.is_file():
        raise HTTPException(status_code=404, detail="original image file not found")
    session = getattr(request.state, "auth_session", None)
    activity_logger.info(
        "Image download requested | id=%s | file=%s | bytes=%d | session=%s | client=%s",
        image_id,
        original.name,
        original.stat().st_size,
        session.id if session else "unknown",
        _client_ip(request),
    )
    return FileResponse(original, filename=original.name)


@app.get("/colors")
def colors_endpoint():
    return store.distinct_colors()


@app.get("/objects")
def objects_endpoint():
    return store.distinct_objects()


@app.get("/model/status")
def model_status_endpoint(request: Request):
    _require_local_admin(request)
    return {"installed": is_ram_checkpoint_installed()}


@app.post("/model/download")
def model_download_endpoint(request: Request):
    _require_local_admin(request)
    if is_ram_checkpoint_installed():
        raise HTTPException(status_code=409, detail="RAM++ checkpoint is already installed")
    with _model_download_jobs_lock:
        if any(not job.done for job in model_download_jobs.values()):
            raise HTTPException(status_code=409, detail="a model download is already running")
        if len(model_download_jobs) >= MAX_MODEL_DOWNLOAD_JOB_HISTORY:
            remove_count = len(model_download_jobs) - MAX_MODEL_DOWNLOAD_JOB_HISTORY + 1
            for old_id in list(model_download_jobs.keys())[:remove_count]:
                del model_download_jobs[old_id]
        job = ModelDownloadJob(id=uuid.uuid4().hex)
        model_download_jobs[job.id] = job
    thread = threading.Thread(target=run_download, args=(job,), daemon=True)
    thread.start()
    return {"job_id": job.id}


@app.get("/model/download/status/{job_id}")
def model_download_status(request: Request, job_id: str):
    _require_local_admin(request)
    with _model_download_jobs_lock:
        job = model_download_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return {
        "downloaded_bytes": job.downloaded_bytes, "total_bytes": job.total_bytes,
        "done": job.done, "error": job.error, "cancelled": job.cancelled,
    }


@app.post("/model/download/{job_id}/cancel")
def model_download_cancel(request: Request, job_id: str):
    _require_local_admin(request)
    with _model_download_jobs_lock:
        job = model_download_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    if job.done:
        raise HTTPException(status_code=409, detail="job already finished")
    job.cancel_event.set()
    return {"status": "cancelling"}


class SettingsUpdate(BaseModel):
    ram_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    ram_custom_tags: list[str] | None = None
    images_dir: str | None = None

    @field_validator("images_dir")
    @classmethod
    def _reject_missing_dir(cls, value: str | None) -> str | None:
        if value is not None and not Path(value).is_dir():
            raise ValueError(f"images_dir {value!r} is not an existing directory")
        return value

    @field_validator("ram_custom_tags")
    @classmethod
    def _reject_path_like_tags(cls, value: list[str] | None) -> list[str] | None:
        # ram_custom_tags feeds directly into a filesystem path lookup
        # (objects.py's reference-image directory), so a tag containing a
        # path separator or ".." must never be accepted here at all.
        if value is None:
            return value
        for tag in value:
            if "/" in tag or "\\" in tag or ".." in tag:
                raise ValueError(f"invalid custom tag {tag!r}: must not contain path separators")
        return value


def _settings_dict() -> dict:
    return {
        "ram_confidence": config.RAM_CONFIDENCE,
        "ram_custom_tags": config.RAM_CUSTOM_TAGS,
        "images_dir": str(config.IMAGES_DIR),
    }


@app.get("/settings")
def get_settings(request: Request):
    _require_local_admin(request)
    return _settings_dict()


@app.post("/settings")
def update_settings(request: Request, update: SettingsUpdate):
    _require_local_admin(request)
    # model_fields_set (not "is not None") distinguishes "field present in the
    # request body, even as an explicit null" from "field omitted entirely" —
    # ram_confidence needs that distinction since null is how the frontend
    # clears it back to "use the model's own defaults", which is a real,
    # meaningful value here, not the same thing as "leave it untouched".
    with _settings_lock:
        fields_set = update.model_fields_set
        next_confidence = (
            update.ram_confidence if "ram_confidence" in fields_set else config.RAM_CONFIDENCE
        )
        next_tags = (
            update.ram_custom_tags
            if update.ram_custom_tags is not None
            else config.RAM_CUSTOM_TAGS
        )
        next_images_dir = (
            Path(update.images_dir) if update.images_dir is not None else config.IMAGES_DIR
        )
        images_dir_changed = next_images_dir != config.IMAGES_DIR

        # Persist the complete next snapshot before changing live objects. If
        # the atomic settings write fails, the running configuration and NAS
        # watcher remain untouched.
        if images_dir_changed:
            with _jobs_lock:
                if any(not job.done for job in jobs.values()):
                    raise HTTPException(
                        status_code=409,
                        detail="cannot change images_dir while a reindex job is running",
                    )
            if not _catalog_run_lock.acquire(blocking=False):
                raise HTTPException(
                    status_code=409,
                    detail="cannot change images_dir while reconciliation is running",
                )
            try:
                # Recheck after reserving the catalog: a manual job may have
                # been submitted between the first check and this lock.
                with _jobs_lock:
                    if any(not job.done for job in jobs.values()):
                        raise HTTPException(
                            status_code=409,
                            detail="cannot change images_dir while a reindex job is running",
                        )
                config.save_settings(next_images_dir, next_confidence, next_tags)
                _stop_realtime_watcher()
                config.IMAGES_DIR = next_images_dir
                indexer.images_dir = next_images_dir
                indexer._last_reconcile_missing.clear()
                config.RAM_CONFIDENCE = next_confidence
                config.RAM_CUSTOM_TAGS = next_tags
                indexer.custom_tags = next_tags
                _start_realtime_watcher()
            finally:
                _catalog_run_lock.release()
        else:
            config.save_settings(next_images_dir, next_confidence, next_tags)
            config.RAM_CONFIDENCE = next_confidence
            config.RAM_CUSTOM_TAGS = next_tags
            indexer.custom_tags = next_tags
        return _settings_dict()


FRONTEND_DIST_DIR = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if FRONTEND_DIST_DIR.is_dir():
    # Mount last so concrete API routes always win. The login shell and hashed
    # assets are deliberately public; every data-bearing API remains protected.
    app.mount("/", StaticFiles(directory=FRONTEND_DIST_DIR, html=True), name="frontend")
else:
    logger.warning(
        "production frontend build not found at %s; run npm run build:frontend",
        FRONTEND_DIST_DIR,
    )
