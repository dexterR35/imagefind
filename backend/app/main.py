import csv
import io
import logging
import math
import hmac
import json
import tempfile
import threading
import time
import unicodedata
import uuid
import zipfile
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from starlette.background import BackgroundTask

SortOption = Literal["date_desc", "date_asc", "name_asc", "name_desc", "size_desc", "size_asc"]

from . import config
from .auth import AuthSession, AuthStore, MAX_PASSWORD_BYTES
from .indexer import Indexer, ReindexJob
from .model_download import ModelDownloadJob, is_ram_checkpoint_installed, run_download
from .rate_limit import SlidingWindowRateLimiter
from .search import find_duplicate_groups as run_find_duplicate_groups
from .search import find_similar as run_find_similar
from .search import search as run_search
from .search import search_semantic as run_search_semantic
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
        # Replacements retain their image id and overwrite the same thumbnail
        # file. A versioned URL makes browsers fetch the new bytes immediately
        # instead of serving the old cached /thumbnail/{id} response.
        "id": e.id,
        "path": e.path,
        "thumbnail_url": f"/thumbnail/{e.id}?v={int(e.indexed_at * 1000)}",
        "ocr_text": e.ocr_text, "objects": e.objects,
        "width": e.width, "height": e.height, "format": e.format,
        "size": e.size, "mtime": e.mtime, "date_taken": e.date_taken,
        "indexed_at": e.indexed_at,
    }


_EMPTY_ANNOTATION = {"favorite": False, "user_tags": [], "note": ""}


def _entries_with_annotations(results) -> list[dict]:
    """Attach each viewer-curated field (favorite / user_tags / note) to the
    serialized image dicts in one batched lookup."""
    entries = [_entry_to_dict(e) for e in results]
    annotations = store.get_annotations([entry["id"] for entry in entries])
    for entry in entries:
        entry.update(annotations.get(entry["id"], _EMPTY_ANNOTATION))
    return entries


def _sanitize_search_value(value: str | None, field: str) -> str | None:
    if value is None:
        return None
    value = unicodedata.normalize("NFC", value).strip()
    if any(unicodedata.category(character) == "Cc" for character in value):
        raise HTTPException(status_code=422, detail=f"{field} contains control characters")
    return value or None


# Mirrors the indexer's IMAGE_EXTENSIONS (plus the alternate spellings).
_ALLOWED_FORMATS = {
    "png", "jpg", "jpeg", "webp", "bmp",
    "gif", "tif", "tiff", "avif", "heic", "heif",
}


def _normalize_format(value: str | None) -> str | None:
    value = _sanitize_search_value(value, "format")
    if value is None:
        return None
    value = value.lower().lstrip(".")
    if value not in _ALLOWED_FORMATS:
        raise HTTPException(status_code=422, detail=f"unsupported format {value!r}")
    return value


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


DateField = Literal["date_taken", "mtime", "indexed_at"]

EXPORT_MAX_ROWS = 50_000


@dataclass
class SearchQuery:
    text: str | None
    obj: str | None
    fmt: str | None
    size_min: int | None
    size_max: int | None
    date_field: str
    date_from: float | None
    date_to: float | None
    width_min: int | None
    width_max: int | None
    height_min: int | None
    height_max: int | None
    orientation: str | None
    favorite: bool | None
    collection: str | None
    user_tag: str | None
    sort: str


def search_query(
    text: str | None = Query(None, max_length=200),
    object: str | None = Query(None, max_length=128),
    format: str | None = Query(None, max_length=16),
    size_min: int | None = Query(None, ge=0, le=1_000_000_000_000),
    size_max: int | None = Query(None, ge=0, le=1_000_000_000_000),
    date_field: DateField = "date_taken",
    date_from: float | None = Query(None, ge=0, le=100_000_000_000),
    date_to: float | None = Query(None, ge=0, le=100_000_000_000),
    width_min: int | None = Query(None, ge=0, le=1_000_000),
    width_max: int | None = Query(None, ge=0, le=1_000_000),
    height_min: int | None = Query(None, ge=0, le=1_000_000),
    height_max: int | None = Query(None, ge=0, le=1_000_000),
    orientation: Literal["portrait", "landscape", "square"] | None = None,
    favorite: bool | None = None,
    collection: str | None = Query(None, max_length=64),
    user_tag: str | None = Query(None, max_length=80),
    sort: SortOption = "date_desc",
) -> SearchQuery:
    return SearchQuery(
        text=_sanitize_search_value(text, "text"),
        obj=_sanitize_search_value(object, "object"),
        fmt=_normalize_format(format),
        size_min=size_min,
        size_max=size_max,
        date_field=date_field,
        date_from=date_from,
        date_to=date_to,
        width_min=width_min,
        width_max=width_max,
        height_min=height_min,
        height_max=height_max,
        orientation=orientation,
        favorite=favorite or None,
        collection=collection,
        user_tag=_sanitize_search_value(user_tag, "user_tag"),
        sort=sort,
    )


def _run_search_query(query: SearchQuery, offset: int, limit: int):
    return run_search(
        store,
        text=query.text,
        obj=query.obj,
        fmt=query.fmt,
        size_min=query.size_min,
        size_max=query.size_max,
        date_field=query.date_field,
        date_from=query.date_from,
        date_to=query.date_to,
        width_min=query.width_min,
        width_max=query.width_max,
        height_min=query.height_min,
        height_max=query.height_max,
        orientation=query.orientation,
        favorite=query.favorite,
        collection=query.collection,
        user_tag=query.user_tag,
        sort=query.sort,
        offset=offset,
        limit=limit,
    )


SEMANTIC_MAX_RESULTS = 120


@app.get("/search")
def search_endpoint(
    request: Request,
    query: SearchQuery = Depends(search_query),
    mode: Literal["text", "semantic"] = "text",
    offset: int = Query(0, ge=0, le=10_000_000),
    limit: int = Query(60, ge=1, le=200),
):
    _enforce_search_rate_limit(request)
    if mode == "semantic" and query.text:
        # CLIP text→image ranking: one fixed nearest-neighbour set, no facets,
        # no paging (like Find Similar) — so the client's page-size `limit` and
        # `offset` don't apply.
        from . import embeddings

        vector = embeddings.embed_text(query.text)
        results = run_search_semantic(store, vector, limit=SEMANTIC_MAX_RESULTS)
        entries = _entries_with_annotations(results)
        return {"results": entries, "total": len(entries)}
    results, total = _run_search_query(query, offset, limit)
    return {"results": _entries_with_annotations(results), "total": total}


@app.get("/duplicates")
def duplicates_endpoint(
    request: Request,
    threshold: float = Query(0.08, ge=0.0, le=1.0),
    max_images: int = Query(5000, ge=2, le=20_000),
):
    _enforce_search_rate_limit(request)
    groups = run_find_duplicate_groups(store, threshold=threshold, max_images=max_images)
    return [_entries_with_annotations(group) for group in groups]


def _iso(timestamp: float | None) -> str:
    if not timestamp:
        return ""
    try:
        return datetime.fromtimestamp(float(timestamp), tz=timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return ""


_EXPORT_HEADER = [
    "id", "path", "filename", "format", "width", "height", "size",
    "date_taken", "mtime", "indexed_at", "objects", "user_tags", "favorite", "note", "ocr_text",
]


def _export_row(entry: dict) -> dict:
    return {
        "id": entry["id"],
        "path": entry["path"],
        "filename": Path(entry["path"]).name,
        "format": entry["format"],
        "width": entry["width"],
        "height": entry["height"],
        "size": entry["size"],
        "date_taken": _iso(entry["date_taken"]),
        "mtime": _iso(entry["mtime"]),
        "indexed_at": _iso(entry["indexed_at"]),
        "objects": entry["objects"],
        "user_tags": entry["user_tags"],
        "favorite": entry["favorite"],
        "note": entry["note"],
        "ocr_text": entry["ocr_text"],
    }


@app.get("/search/export")
def search_export_endpoint(
    request: Request,
    query: SearchQuery = Depends(search_query),
    output: Literal["csv", "json"] = "csv",
    limit: int = Query(EXPORT_MAX_ROWS, ge=1, le=EXPORT_MAX_ROWS),
):
    _enforce_download_rate_limit(request)
    results, _ = _run_search_query(query, 0, limit)
    rows = [_export_row(entry) for entry in _entries_with_annotations(results)]
    stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d-%H%M%S")

    if output == "json":
        def stream_json():
            yield "["
            for index, row in enumerate(rows):
                yield ("," if index else "") + json.dumps(row, separators=(",", ":"))
            yield "]"

        media_type = "application/json"
        content = stream_json()
        extension = "json"
    else:
        def stream_csv():
            buffer = io.StringIO()
            writer = csv.writer(buffer)
            for record in (_EXPORT_HEADER, *(
                [
                    "; ".join(row[key]) if isinstance(row[key], list) else row[key]
                    for key in _EXPORT_HEADER
                ]
                for row in rows
            )):
                writer.writerow(record)
                yield buffer.getvalue()
                buffer.seek(0)
                buffer.truncate(0)

        media_type = "text/csv"
        content = stream_csv()
        extension = "csv"

    return StreamingResponse(
        content,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="imagefind-export-{stamp}.{extension}"',
            "Cache-Control": "no-store",
        },
    )


@app.get("/search/similar/{image_id}")
def similar_endpoint(request: Request, image_id: str):
    _enforce_search_rate_limit(request)
    results = run_find_similar(store, image_id)
    if results is None:
        raise HTTPException(status_code=404, detail="image not found")
    return _entries_with_annotations(results)


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
    return _reindex_job_payload(job)


def _reindex_job_payload(job: ReindexJob) -> dict:
    return {
        "processed": job.processed, "total": job.total, "failed": job.failed,
        "done": job.done, "error": job.error, "cancelled": job.cancelled,
        "failures": job.failures,
    }


@app.get("/reindex/status/{job_id}/stream")
def reindex_status_stream(request: Request, job_id: str):
    """Server-sent events version of the status poll: one `data:` frame per
    tick until the job finishes, then the stream closes."""
    _require_local_admin(request)
    with _jobs_lock:
        job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")

    def events():
        while True:
            yield f"data: {json.dumps(_reindex_job_payload(job))}\n\n"
            if job.done:
                return
            time.sleep(0.5)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


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
    # A grid re-render otherwise revalidates every visible thumbnail on every
    # paint. The file is rewritten in place (same URL) when its source image
    # changes, so keep the window short and let FileResponse's ETag handle
    # revalidation after it expires.
    return FileResponse(
        thumbnail, headers={"Cache-Control": "private, max-age=300, stale-while-revalidate=86400"}
    )


@app.get("/image/{image_id}")
def image_endpoint(request: Request, image_id: str):
    """Serve the full-resolution original inline, for the detail view's
    zoom/pan preview (the thumbnail is only ~320px and blurs when magnified)."""
    _enforce_download_rate_limit(request)
    entry = store.get(image_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="image not found")
    original = Path(entry.path)
    if not original.is_file():
        raise HTTPException(status_code=404, detail="original image file not found")
    return FileResponse(
        original,
        headers={
            "Cache-Control": "private, max-age=3600",
            "Content-Disposition": "inline",
        },
    )


@app.get("/stats")
def stats_endpoint(request: Request):
    _enforce_search_rate_limit(request)
    return store.stats()


MAX_ZIP_IMAGES = 500


@app.get("/download/zip")
def download_zip_endpoint(request: Request, ids: str = Query(..., max_length=MAX_ZIP_IMAGES * 40)):
    """Bundle the originals for a bulk selection into a single zip download."""
    _enforce_download_rate_limit(request)
    image_ids = [value for value in ids.split(",") if value][:MAX_ZIP_IMAGES]
    if not image_ids:
        raise HTTPException(status_code=422, detail="no image ids given")

    archive = tempfile.NamedTemporaryFile(prefix="imagefind-zip-", suffix=".zip", delete=False)
    used_names: set[str] = set()
    written = 0
    try:
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as bundle:
            for image_id in image_ids:
                entry = store.get(image_id)
                if entry is None:
                    continue
                source = Path(entry.path)
                if not source.is_file():
                    continue
                name = source.name
                if name in used_names:
                    name = f"{source.stem}_{image_id[:8]}{source.suffix}"
                used_names.add(name)
                bundle.write(source, arcname=name)
                written += 1
        archive.close()
    except Exception:
        archive.close()
        Path(archive.name).unlink(missing_ok=True)
        raise
    if written == 0:
        Path(archive.name).unlink(missing_ok=True)
        raise HTTPException(status_code=404, detail="none of the selected originals are available")

    stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d-%H%M%S")
    activity_logger.info(
        "Bulk zip download | files=%d | client=%s", written, _client_ip(request),
    )
    return FileResponse(
        archive.name,
        media_type="application/zip",
        filename=f"imagefind-{stamp}.zip",
        headers={"Cache-Control": "no-store"},
        background=BackgroundTask(lambda: Path(archive.name).unlink(missing_ok=True)),
    )


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
    return FileResponse(
        original,
        filename=original.name,
        headers={"Cache-Control": "private, max-age=3600"},
    )


@app.get("/objects")
def objects_endpoint():
    return store.distinct_objects()


@app.get("/user-tags")
def user_tags_endpoint(request: Request):
    return store.distinct_user_tags()


MAX_USER_TAG_LENGTH = 60
MAX_USER_TAGS = 50
MAX_NOTE_LENGTH = 5000
MAX_BULK_IMAGE_IDS = 1000


def _clean_tag_list(value: list[str]) -> list[str]:
    cleaned: list[str] = []
    for tag in value:
        tag = unicodedata.normalize("NFC", tag).strip()
        if not tag:
            continue
        if len(tag) > MAX_USER_TAG_LENGTH:
            raise ValueError(f"tag {tag!r} exceeds {MAX_USER_TAG_LENGTH} characters")
        if any(unicodedata.category(ch) == "Cc" for ch in tag):
            raise ValueError("tags must not contain control characters")
        cleaned.append(tag)
    return cleaned


class FavoriteUpdate(BaseModel):
    favorite: bool


class TagsUpdate(BaseModel):
    tags: list[str] = Field(default_factory=list, max_length=MAX_USER_TAGS)

    @field_validator("tags")
    @classmethod
    def _clean_tags(cls, value: list[str]) -> list[str]:
        return _clean_tag_list(value)


class NoteUpdate(BaseModel):
    note: str = Field(default="", max_length=MAX_NOTE_LENGTH)


class CollectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class CollectionImages(BaseModel):
    image_ids: list[str] = Field(min_length=1, max_length=MAX_BULK_IMAGE_IDS)


class BulkFavorite(BaseModel):
    image_ids: list[str] = Field(min_length=1, max_length=MAX_BULK_IMAGE_IDS)
    favorite: bool


class BulkTags(BaseModel):
    image_ids: list[str] = Field(min_length=1, max_length=MAX_BULK_IMAGE_IDS)
    tags: list[str] = Field(min_length=1, max_length=MAX_USER_TAGS)

    @field_validator("tags")
    @classmethod
    def _clean_tags(cls, value: list[str]) -> list[str]:
        return _clean_tag_list(value)


def _require_image(image_id: str) -> None:
    if store.get(image_id) is None:
        raise HTTPException(status_code=404, detail="image not found")


@app.put("/images/{image_id}/favorite")
def set_favorite_endpoint(request: Request, image_id: str, body: FavoriteUpdate):
    _require_image(image_id)
    store.set_favorite(image_id, body.favorite)
    return {"favorite": body.favorite}


@app.post("/images/favorite")
def bulk_favorite_endpoint(request: Request, body: BulkFavorite):
    return {"changed": store.set_favorites(body.image_ids, body.favorite)}


@app.post("/images/tags/add")
def bulk_add_tags_endpoint(request: Request, body: BulkTags):
    return {"added": store.add_user_tags(body.image_ids, body.tags)}


@app.put("/images/{image_id}/tags")
def set_tags_endpoint(request: Request, image_id: str, body: TagsUpdate):
    _require_image(image_id)
    return {"user_tags": store.set_user_tags(image_id, body.tags)}


@app.put("/images/{image_id}/note")
def set_note_endpoint(request: Request, image_id: str, body: NoteUpdate):
    _require_image(image_id)
    return {"note": store.set_note(image_id, body.note)}


@app.get("/collections")
def list_collections_endpoint(request: Request):
    return store.list_collections()


@app.post("/collections")
def create_collection_endpoint(request: Request, body: CollectionCreate):
    try:
        return store.create_collection(body.name)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@app.patch("/collections/{collection_id}")
def rename_collection_endpoint(request: Request, collection_id: str, body: CollectionCreate):
    try:
        renamed = store.rename_collection(collection_id, body.name)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    if not renamed:
        raise HTTPException(status_code=404, detail="collection not found")
    return {"status": "ok"}


@app.delete("/collections/{collection_id}")
def delete_collection_endpoint(request: Request, collection_id: str):
    if not store.delete_collection(collection_id):
        raise HTTPException(status_code=404, detail="collection not found")
    return {"status": "ok"}


@app.post("/collections/{collection_id}/images")
def add_collection_images_endpoint(request: Request, collection_id: str, body: CollectionImages):
    try:
        added = store.add_to_collection(collection_id, body.image_ids)
    except KeyError:
        raise HTTPException(status_code=404, detail="collection not found")
    return {"added": added}


@app.delete("/collections/{collection_id}/images")
def remove_collection_images_endpoint(request: Request, collection_id: str, body: CollectionImages):
    if not store.collection_exists(collection_id):
        raise HTTPException(status_code=404, detail="collection not found")
    return {"removed": store.remove_from_collection(collection_id, body.image_ids)}


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


MAX_BACKUPS = 10


def _backup_dir() -> Path:
    return config.INDEX_DIR / "backups"


def _backup_info(path: Path) -> dict:
    stat = path.stat()
    return {"name": path.name, "size": stat.st_size, "created_at": stat.st_mtime}


@app.get("/backup")
def list_backups_endpoint(request: Request):
    _require_local_admin(request)
    directory = _backup_dir()
    if not directory.is_dir():
        return []
    return sorted(
        (_backup_info(path) for path in directory.glob("index-*.db")),
        key=lambda item: item["created_at"],
        reverse=True,
    )


@app.post("/backup")
def create_backup_endpoint(request: Request):
    _require_local_admin(request)
    directory = _backup_dir()
    stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d-%H%M%S")
    destination = directory / f"index-{stamp}.db"
    store.backup(destination)
    # Keep only the most recent MAX_BACKUPS so this cannot fill the disk.
    existing = sorted(directory.glob("index-*.db"), key=lambda p: p.stat().st_mtime)
    for stale in existing[:-MAX_BACKUPS]:
        stale.unlink(missing_ok=True)
    activity_logger.info(
        "Index backup written | file=%s | bytes=%d | client=%s",
        destination.name, destination.stat().st_size, _client_ip(request),
    )
    return _backup_info(destination)


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
