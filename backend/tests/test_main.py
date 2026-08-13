import importlib
import time

import numpy as np
from fastapi.testclient import TestClient


def _fresh_app(tmp_path, monkeypatch):
    images_dir = tmp_path / "images"
    images_dir.mkdir()
    monkeypatch.setenv("IMAGES_DIR", str(images_dir))
    monkeypatch.setenv("INDEX_DIR", str(tmp_path / "index"))
    from app import config, main
    importlib.reload(config)
    importlib.reload(main)
    return main, images_dir


def test_health_reports_zero_indexed(tmp_path, monkeypatch):
    main, _ = _fresh_app(tmp_path, monkeypatch)
    client = TestClient(main.app)
    assert client.get("/health").json() == {"status": "ok", "indexed": 0}


def test_reindex_on_empty_folder_completes_immediately(tmp_path, monkeypatch):
    main, _ = _fresh_app(tmp_path, monkeypatch)
    client = TestClient(main.app)
    job_id = client.post("/reindex").json()["job_id"]

    status = {}
    for _ in range(40):
        status = client.get(f"/reindex/status/{job_id}").json()
        if status["done"]:
            break
        time.sleep(0.05)

    assert status == {"processed": 0, "total": 0, "done": True, "error": None}


def test_search_and_filters_use_prepopulated_store(tmp_path, monkeypatch):
    main, _ = _fresh_app(tmp_path, monkeypatch)
    from app.storage import ImageEntry

    entry = ImageEntry(
        id="a1", path="/imgs/a.png", thumbnail_path=str(tmp_path / "a1.jpg"),
        ocr_text="NETBET", colors=["green"], objects=["clover"], mtime=0.0, size=0,
    )
    (tmp_path / "a1.jpg").write_bytes(b"fake-jpg-bytes")
    main.store.upsert(entry, np.ones(512, dtype=np.float32))

    client = TestClient(main.app)
    assert client.get("/colors").json() == ["green"]
    assert client.get("/objects").json() == ["clover"]

    result = client.get("/search", params={"color": "green"}).json()
    assert [r["id"] for r in result] == ["a1"]

    assert client.get("/search/similar/a1").json() == []
    assert client.get("/search/similar/missing").status_code == 404


def test_thumbnail_serves_cached_file(tmp_path, monkeypatch):
    main, _ = _fresh_app(tmp_path, monkeypatch)
    from app.storage import ImageEntry

    thumb_path = tmp_path / "thumb.jpg"
    thumb_path.write_bytes(b"fake-jpg-bytes")
    entry = ImageEntry(
        id="a1", path="/imgs/a.png", thumbnail_path=str(thumb_path),
        ocr_text="", colors=[], objects=[], mtime=0.0, size=0,
    )
    main.store.upsert(entry, np.ones(512, dtype=np.float32))

    client = TestClient(main.app)
    response = client.get("/thumbnail/a1")
    assert response.status_code == 200
    assert response.content == b"fake-jpg-bytes"
    assert client.get("/thumbnail/missing").status_code == 404
