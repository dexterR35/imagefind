import threading
import uuid

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
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

store = IndexStore(config.INDEX_DIR)
store.load()
indexer = Indexer(config.IMAGES_DIR, config.INDEX_DIR, store, config.RAM_CUSTOM_TAGS)
jobs: dict[str, ReindexJob] = {}
MAX_JOB_HISTORY = 10


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
    return _settings_dict()
