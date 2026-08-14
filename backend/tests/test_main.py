import importlib
import threading
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

    assert status == {"processed": 0, "total": 0, "failed": 0, "done": True, "error": None}


def test_second_reindex_while_one_is_running_returns_409(tmp_path, monkeypatch):
    main, _ = _fresh_app(tmp_path, monkeypatch)
    release = threading.Event()

    def fake_run_reindex(job, force=False):
        release.wait(timeout=5)
        job.done = True

    monkeypatch.setattr(main.indexer, "run_reindex", fake_run_reindex)
    client = TestClient(main.app)

    first = client.post("/reindex")
    assert first.status_code == 200

    try:
        second = client.post("/reindex")
        assert second.status_code == 409
    finally:
        release.set()


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


def test_get_settings_returns_current_config_values(tmp_path, monkeypatch):
    main, _ = _fresh_app(tmp_path, monkeypatch)
    client = TestClient(main.app)
    settings = client.get("/settings").json()
    assert settings == {
        "yolo_confidence": main.config.YOLO_CONFIDENCE,
        "owl_confidence": main.config.OWL_CONFIDENCE,
        "text_similarity_threshold": main.config.TEXT_SIMILARITY_THRESHOLD,
        "color_clusters": main.config.COLOR_CLUSTERS,
        "color_min_share": main.config.COLOR_MIN_SHARE,
        "vocabulary": main.config.VOCABULARY,
    }


def test_post_settings_updates_config_and_indexer_vocabulary(tmp_path, monkeypatch):
    main, _ = _fresh_app(tmp_path, monkeypatch)
    client = TestClient(main.app)

    response = client.post("/settings", json={
        "owl_confidence": 0.05,
        "color_min_share": 0.03,
        "vocabulary": ["diamond", "scatter"],
    })
    assert response.status_code == 200
    assert response.json()["owl_confidence"] == 0.05
    assert response.json()["vocabulary"] == ["diamond", "scatter"]

    # Takes effect immediately, without restart, and syncs the already-built
    # Indexer instance (which snapshots vocabulary at construction time).
    assert main.config.OWL_CONFIDENCE == 0.05
    assert main.config.COLOR_MIN_SHARE == 0.03
    assert main.config.VOCABULARY == ["diamond", "scatter"]
    assert main.indexer.vocabulary == ["diamond", "scatter"]

    # Fields not included in the request are left untouched.
    assert main.config.YOLO_CONFIDENCE == 0.4


def test_post_settings_rejects_out_of_range_values(tmp_path, monkeypatch):
    main, _ = _fresh_app(tmp_path, monkeypatch)
    client = TestClient(main.app)

    # color_clusters=0 would crash sklearn's KMeans on every image during
    # the next reindex if it were ever allowed through.
    response = client.post("/settings", json={"color_clusters": 0})
    assert response.status_code == 422
    assert main.config.COLOR_CLUSTERS != 0

    response = client.post("/settings", json={"color_min_share": 1.5})
    assert response.status_code == 422

    response = client.post("/settings", json={"owl_confidence": -0.1})
    assert response.status_code == 422

    # Config is untouched by a rejected request.
    assert main.config.COLOR_CLUSTERS == 4
    assert main.config.COLOR_MIN_SHARE == 0.08
    assert main.config.OWL_CONFIDENCE == 0.15


def test_reindex_force_param_is_passed_through_to_indexer(tmp_path, monkeypatch):
    main, _ = _fresh_app(tmp_path, monkeypatch)
    calls = []

    def fake_run_reindex(job, force=False):
        calls.append(force)
        job.done = True

    monkeypatch.setattr(main.indexer, "run_reindex", fake_run_reindex)
    client = TestClient(main.app)

    def run_and_wait(**params):
        job_id = client.post("/reindex", params=params).json()["job_id"]
        for _ in range(40):
            if client.get(f"/reindex/status/{job_id}").json()["done"]:
                return
            time.sleep(0.05)

    run_and_wait()
    run_and_wait(force="true")

    assert calls == [False, True]


def test_jobs_history_is_capped_on_a_long_running_server(tmp_path, monkeypatch):
    main, _ = _fresh_app(tmp_path, monkeypatch)

    def instant_run_reindex(job, force=False):
        job.done = True

    monkeypatch.setattr(main.indexer, "run_reindex", instant_run_reindex)
    client = TestClient(main.app)

    for _ in range(main.MAX_JOB_HISTORY + 5):
        response = client.post("/reindex")
        assert response.status_code == 200

    assert len(main.jobs) <= main.MAX_JOB_HISTORY


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
