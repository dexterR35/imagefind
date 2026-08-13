import threading
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

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
indexer = Indexer(config.IMAGES_DIR, config.INDEX_DIR, store, config.VOCABULARY)
jobs: dict[str, ReindexJob] = {}


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
    if store.get(image_id) is None:
        raise HTTPException(status_code=404, detail="image not found")
    results = run_find_similar(store, image_id)
    return [_entry_to_dict(e) for e in results]


@app.post("/reindex")
def reindex_endpoint():
    if any(not j.done for j in jobs.values()):
        raise HTTPException(status_code=409, detail="a reindex job is already running")
    job = ReindexJob(id=uuid.uuid4().hex)
    jobs[job.id] = job
    thread = threading.Thread(target=indexer.run_reindex, args=(job,), daemon=True)
    thread.start()
    return {"job_id": job.id}


@app.get("/reindex/status/{job_id}")
def reindex_status(job_id: str):
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return {"processed": job.processed, "total": job.total, "done": job.done, "error": job.error}


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
