import threading
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator

from . import config
from .indexer import Indexer, ReindexJob
from .search import find_similar as run_find_similar
from .search import search as run_search
from .storage import IndexStore

app = FastAPI(title="ImageFind")
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

_watcher_observer = None
_reconciliation_stop = threading.Event()
_reconciliation_thread = None

if config.ENABLE_WATCHER:
    from .watcher import start_reconciliation_loop, start_watcher

    _watcher_observer = start_watcher(indexer, store)
    _reconciliation_thread = start_reconciliation_loop(
        indexer,
        lambda: ReindexJob(id=uuid.uuid4().hex),
        config.RECONCILE_INTERVAL_SECONDS,
        _reconciliation_stop,
    )


@app.on_event("shutdown")
def _stop_watcher():
    if _watcher_observer is not None:
        _watcher_observer.stop()
        _watcher_observer.join(timeout=5)
    _reconciliation_stop.set()
    if _reconciliation_thread is not None:
        _reconciliation_thread.join(timeout=5)


def _entry_to_dict(e) -> dict:
    return {
        "id": e.id, "path": e.path, "thumbnail_url": f"/thumbnail/{e.id}",
        "ocr_text": e.ocr_text, "colors": e.colors, "objects": e.objects,
    }


@app.get("/health")
def health():
    return {"status": "ok", "indexed": len(store.all())}


@app.get("/search")
def search_endpoint(text: str | None = None, color: str | None = None, object: str | None = None):
    results = run_search(store, text=text, color=color, obj=object)
    return [_entry_to_dict(e) for e in results]


@app.get("/search/similar/{image_id}")
def similar_endpoint(image_id: str):
    results = run_find_similar(store, image_id)
    if results is None:
        raise HTTPException(status_code=404, detail="image not found")
    return [_entry_to_dict(e) for e in results]


@app.post("/reindex")
def reindex_endpoint(force: bool = False):
    if any(not j.done for j in jobs.values()):
        raise HTTPException(status_code=409, detail="a reindex job is already running")
    # Evict the oldest completed jobs so `jobs` doesn't grow unbounded on a
    # long-running server where reindex gets triggered repeatedly.
    if len(jobs) >= MAX_JOB_HISTORY:
        for old_id in list(jobs.keys())[: len(jobs) - MAX_JOB_HISTORY + 1]:
            del jobs[old_id]
    job = ReindexJob(id=uuid.uuid4().hex)
    jobs[job.id] = job
    thread = threading.Thread(target=indexer.run_reindex, args=(job, force), daemon=True)
    thread.start()
    return {"job_id": job.id}


@app.get("/reindex/status/{job_id}")
def reindex_status(job_id: str):
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return {
        "processed": job.processed, "total": job.total, "failed": job.failed,
        "done": job.done, "error": job.error,
    }


@app.get("/thumbnail/{image_id}")
def thumbnail_endpoint(image_id: str):
    entry = store.get(image_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="image not found")
    return FileResponse(entry.thumbnail_path)


@app.get("/colors")
def colors_endpoint():
    return sorted({c for e in store.all() for c in e.colors})


@app.get("/objects")
def objects_endpoint():
    return sorted({o for e in store.all() for o in e.objects})


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
def get_settings():
    return _settings_dict()


@app.post("/settings")
def update_settings(update: SettingsUpdate):
    # model_fields_set (not "is not None") distinguishes "field present in the
    # request body, even as an explicit null" from "field omitted entirely" —
    # ram_confidence needs that distinction since null is how the frontend
    # clears it back to "use the model's own defaults", which is a real,
    # meaningful value here, not the same thing as "leave it untouched".
    fields_set = update.model_fields_set
    if "ram_confidence" in fields_set:
        config.RAM_CONFIDENCE = update.ram_confidence
    if update.ram_custom_tags is not None:
        config.RAM_CUSTOM_TAGS = update.ram_custom_tags
        # Indexer snapshots custom_tags at construction time, so a runtime
        # change to config.RAM_CUSTOM_TAGS needs to be pushed to it
        # explicitly to actually take effect on the next reindex.
        indexer.custom_tags = update.ram_custom_tags
    if update.images_dir is not None:
        config.IMAGES_DIR = Path(update.images_dir)
        indexer.images_dir = config.IMAGES_DIR
    # Persists the full current snapshot (not just whichever field this call
    # touched) so ram_confidence/ram_custom_tags survive a restart the same
    # way images_dir already did, regardless of which fields this particular
    # request set.
    config.save_settings(config.IMAGES_DIR, config.RAM_CONFIDENCE, config.RAM_CUSTOM_TAGS)
    return _settings_dict()
