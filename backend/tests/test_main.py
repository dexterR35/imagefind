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
    # Real-time NAS indexing is enabled by default in the application. Tests
    # explicitly disable it so module reloads never leave background threads.
    monkeypatch.setenv("ENABLE_WATCHER", "false")
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

    assert status == {
        "processed": 0, "total": 0, "failed": 0, "done": True, "error": None, "cancelled": False,
    }


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


def test_cancel_reindex_sets_the_job_cancel_event(tmp_path, monkeypatch):
    main, _ = _fresh_app(tmp_path, monkeypatch)
    release = threading.Event()

    def fake_run_reindex(job, force=False):
        release.wait(timeout=5)
        job.done = True

    monkeypatch.setattr(main.indexer, "run_reindex", fake_run_reindex)
    client = TestClient(main.app)

    job_id = client.post("/reindex").json()["job_id"]
    try:
        response = client.post(f"/reindex/{job_id}/cancel")
        assert response.status_code == 200
        assert main.jobs[job_id].cancel_event.is_set()
    finally:
        release.set()


def test_cancel_reindex_returns_404_for_unknown_job(tmp_path, monkeypatch):
    main, _ = _fresh_app(tmp_path, monkeypatch)
    client = TestClient(main.app)
    assert client.post("/reindex/does-not-exist/cancel").status_code == 404


def test_cancel_reindex_returns_409_for_an_already_finished_job(tmp_path, monkeypatch):
    main, _ = _fresh_app(tmp_path, monkeypatch)
    client = TestClient(main.app)
    job_id = client.post("/reindex").json()["job_id"]

    status = {}
    for _ in range(40):
        status = client.get(f"/reindex/status/{job_id}").json()
        if status["done"]:
            break
        time.sleep(0.05)
    assert status["done"] is True

    assert client.post(f"/reindex/{job_id}/cancel").status_code == 409


def test_watcher_can_be_disabled_for_maintenance(tmp_path, monkeypatch):
    main, _ = _fresh_app(tmp_path, monkeypatch)
    assert main._watcher_observer is None
    assert main._reconciliation_thread is None


def test_search_and_filters_use_prepopulated_store(tmp_path, monkeypatch):
    main, _ = _fresh_app(tmp_path, monkeypatch)
    from app.storage import ImageEntry

    entry = ImageEntry(
        id="a1", path="/imgs/a.png", thumbnail_path=str(tmp_path / "a1.jpg"),
        ocr_text="NETBET", colors=["green"], objects=["clover", "person"], mtime=0.0, size=0,
    )
    (tmp_path / "a1.jpg").write_bytes(b"fake-jpg-bytes")
    main.store.upsert(entry, np.ones(512, dtype=np.float32))

    client = TestClient(main.app)
    assert client.get("/colors").json() == ["green"]
    assert client.get("/objects").json() == ["clover", "person"]

    result = client.get("/search", params={"color": "green"}).json()
    assert [r["id"] for r in result["results"]] == ["a1"]
    assert result["total"] == 1

    result = client.get("/search", params={"object": "clover"}).json()
    assert [r["id"] for r in result["results"]] == ["a1"]
    assert client.get("/search", params={"object": "missing"}).json() == {"results": [], "total": 0}

    assert client.get("/search/similar/a1").json() == []
    assert client.get("/search/similar/missing").status_code == 404


def test_search_paginates_and_reports_metadata(tmp_path, monkeypatch):
    main, _ = _fresh_app(tmp_path, monkeypatch)
    from app.storage import ImageEntry

    for i in range(3):
        entry = ImageEntry(
            id=f"e{i}", path=f"/imgs/e{i}.png", thumbnail_path=str(tmp_path / f"e{i}.jpg"),
            ocr_text="", colors=[], objects=[], mtime=0.0, size=100 * i,
            width=10, height=20, format="PNG", date_taken=float(i),
            indexed_at=100.0 + i,
        )
        main.store.upsert(entry, np.ones(512, dtype=np.float32))

    client = TestClient(main.app)
    page = client.get("/search", params={"sort": "date_asc", "offset": 1, "limit": 1}).json()
    assert [r["id"] for r in page["results"]] == ["e1"]
    assert page["total"] == 3
    assert page["results"][0]["width"] == 10
    assert page["results"][0]["format"] == "PNG"
    assert page["results"][0]["indexed_at"] == 101.0

    assert client.get("/search", params={"sort": "not-a-real-sort"}).status_code == 422
    assert client.get("/search", params={"offset": -1}).status_code == 422
    assert client.get("/search", params={"offset": 10_000_001}).status_code == 422
    assert client.get("/search", params={"limit": 0}).status_code == 422
    assert client.get("/search", params={"limit": 1000}).status_code == 422


def test_search_validates_lengths_and_rejects_control_characters(tmp_path, monkeypatch):
    main, _ = _fresh_app(tmp_path, monkeypatch)
    client = TestClient(main.app)

    assert client.get("/search", params={"text": "x" * 201}).status_code == 422
    assert client.get("/search", params={"color": "x" * 65}).status_code == 422
    assert client.get("/search", params={"object": "x" * 129}).status_code == 422
    response = client.get("/search", params={"text": "hello\x01world"})
    assert response.status_code == 422
    assert "control characters" in response.json()["detail"]


def test_search_rate_limit_returns_429_with_retry_after(tmp_path, monkeypatch):
    main, _ = _fresh_app(tmp_path, monkeypatch)
    from app.rate_limit import SlidingWindowRateLimiter

    main._search_rate_limiter = SlidingWindowRateLimiter(2, 60.0)
    client = TestClient(main.app)

    assert client.get("/search").status_code == 200
    assert client.get("/search").status_code == 200
    limited = client.get("/search")
    assert limited.status_code == 429
    assert limited.headers["retry-after"]


def test_get_settings_returns_current_config_values(tmp_path, monkeypatch):
    main, _ = _fresh_app(tmp_path, monkeypatch)
    client = TestClient(main.app)
    settings = client.get("/settings").json()
    assert settings == {
        "ram_confidence": main.config.RAM_CONFIDENCE,
        "ram_custom_tags": main.config.RAM_CUSTOM_TAGS,
        "images_dir": str(main.config.IMAGES_DIR),
    }


def test_post_settings_updates_images_dir_and_indexer(tmp_path, monkeypatch):
    main, _ = _fresh_app(tmp_path, monkeypatch)
    client = TestClient(main.app)
    other_dir = tmp_path / "other_images"
    other_dir.mkdir()

    response = client.post("/settings", json={"images_dir": str(other_dir)})

    assert response.status_code == 200
    assert response.json()["images_dir"] == str(other_dir)
    assert main.config.IMAGES_DIR == other_dir
    # Indexer snapshots images_dir at construction time, so a runtime change
    # needs to be pushed to it explicitly, same as custom_tags.
    assert main.indexer.images_dir == other_dir


def test_post_settings_rejects_images_dir_change_during_reindex(tmp_path, monkeypatch):
    main, images_dir = _fresh_app(tmp_path, monkeypatch)
    from app.indexer import ReindexJob

    main.jobs["active"] = ReindexJob(id="active")
    other_dir = tmp_path / "other_images"
    other_dir.mkdir()

    response = TestClient(main.app).post("/settings", json={"images_dir": str(other_dir)})

    assert response.status_code == 409
    assert main.config.IMAGES_DIR == images_dir
    assert main.indexer.images_dir == images_dir


def test_post_settings_rejects_images_dir_change_during_reconciliation(tmp_path, monkeypatch):
    main, images_dir = _fresh_app(tmp_path, monkeypatch)
    other_dir = tmp_path / "other_images"
    other_dir.mkdir()
    assert main._catalog_run_lock.acquire(blocking=False)
    try:
        response = TestClient(main.app).post("/settings", json={"images_dir": str(other_dir)})
    finally:
        main._catalog_run_lock.release()

    assert response.status_code == 409
    assert main.config.IMAGES_DIR == images_dir
    assert main.indexer.images_dir == images_dir


def test_post_settings_rejects_nonexistent_images_dir(tmp_path, monkeypatch):
    main, images_dir = _fresh_app(tmp_path, monkeypatch)
    client = TestClient(main.app)
    missing_dir = tmp_path / "does_not_exist"

    response = client.post("/settings", json={"images_dir": str(missing_dir)})

    assert response.status_code == 422
    assert main.config.IMAGES_DIR == images_dir
    assert main.indexer.images_dir == images_dir


def test_persisted_images_dir_survives_restart(tmp_path, monkeypatch):
    main, _ = _fresh_app(tmp_path, monkeypatch)
    client = TestClient(main.app)
    other_dir = tmp_path / "other_images"
    other_dir.mkdir()
    assert client.post("/settings", json={"images_dir": str(other_dir)}).status_code == 200

    # Simulate a fresh process: the IMAGES_DIR env var still points at the
    # original folder, but the persisted settings file should take priority.
    importlib.reload(main.config)
    importlib.reload(main)

    assert main.config.IMAGES_DIR == other_dir
    assert main.indexer.images_dir == other_dir


def test_persisted_ram_settings_survive_restart(tmp_path, monkeypatch):
    main, _ = _fresh_app(tmp_path, monkeypatch)
    client = TestClient(main.app)
    assert client.post("/settings", json={
        "ram_confidence": 0.33,
        "ram_custom_tags": ["zeus", "lightning"],
    }).status_code == 200

    # Simulate a fresh process: env vars don't set these, but the persisted
    # settings file should be picked back up on import, same as images_dir.
    importlib.reload(main.config)
    importlib.reload(main)

    assert main.config.RAM_CONFIDENCE == 0.33
    assert main.config.RAM_CUSTOM_TAGS == ["zeus", "lightning"]
    assert main.indexer.custom_tags == ["zeus", "lightning"]


def test_post_settings_updates_config_and_indexer_custom_tags(tmp_path, monkeypatch):
    main, _ = _fresh_app(tmp_path, monkeypatch)
    client = TestClient(main.app)

    response = client.post("/settings", json={
        "ram_confidence": 0.05,
        "ram_custom_tags": ["zeus", "lightning"],
    })
    assert response.status_code == 200
    assert response.json()["ram_confidence"] == 0.05
    assert response.json()["ram_custom_tags"] == ["zeus", "lightning"]

    # Takes effect immediately, without restart, and syncs the already-built
    # Indexer instance (which snapshots custom_tags at construction time).
    assert main.config.RAM_CONFIDENCE == 0.05
    assert main.config.RAM_CUSTOM_TAGS == ["zeus", "lightning"]
    assert main.indexer.custom_tags == ["zeus", "lightning"]


def test_post_settings_rejects_out_of_range_values(tmp_path, monkeypatch):
    main, _ = _fresh_app(tmp_path, monkeypatch)
    client = TestClient(main.app)

    response = client.post("/settings", json={"ram_confidence": -0.1})
    assert response.status_code == 422
    assert main.config.RAM_CONFIDENCE != -0.1

    response = client.post("/settings", json={"ram_confidence": 1.5})
    assert response.status_code == 422


def test_post_settings_explicit_null_clears_ram_confidence(tmp_path, monkeypatch):
    main, _ = _fresh_app(tmp_path, monkeypatch)
    client = TestClient(main.app)

    client.post("/settings", json={"ram_confidence": 0.6})
    assert main.config.RAM_CONFIDENCE == 0.6

    # An explicit null (what the Settings UI sends when the field is blanked
    # to mean "use the model's own defaults") must actually clear it, not be
    # treated the same as the field being omitted from the request entirely.
    response = client.post("/settings", json={"ram_confidence": None})
    assert response.status_code == 200
    assert response.json()["ram_confidence"] is None
    assert main.config.RAM_CONFIDENCE is None


def test_post_settings_omitted_field_leaves_ram_confidence_untouched(tmp_path, monkeypatch):
    main, _ = _fresh_app(tmp_path, monkeypatch)
    client = TestClient(main.app)

    client.post("/settings", json={"ram_confidence": 0.6})
    response = client.post("/settings", json={"ram_custom_tags": ["zeus"]})

    assert response.status_code == 200
    assert main.config.RAM_CONFIDENCE == 0.6


def test_post_settings_rejects_path_like_custom_tags(tmp_path, monkeypatch):
    main, _ = _fresh_app(tmp_path, monkeypatch)
    client = TestClient(main.app)

    for bad_tag in ["../etc", "a/b", "a\\b", "..", "foo/../bar"]:
        response = client.post("/settings", json={"ram_custom_tags": [bad_tag]})
        assert response.status_code == 422, bad_tag
    assert main.config.RAM_CUSTOM_TAGS == []


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


def test_reindex_is_blocked_through_cloudflare_tunnel(tmp_path, monkeypatch):
    main, _ = _fresh_app(tmp_path, monkeypatch)
    client = TestClient(main.app)

    response = client.post(
        "/reindex",
        headers={"Origin": "https://example.trycloudflare.com"},
    )

    assert response.status_code == 403
    assert "disabled through the public tunnel" in response.json()["detail"]
    assert main.jobs == {}


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


def test_thumbnail_returns_404_when_database_file_reference_is_stale(tmp_path, monkeypatch):
    main, _ = _fresh_app(tmp_path, monkeypatch)
    from app.storage import ImageEntry

    main.store.upsert(
        ImageEntry(
            id="stale", path="/imgs/a.png", thumbnail_path=str(tmp_path / "missing.jpg"),
            ocr_text="", colors=[], objects=[], mtime=0.0, size=0,
        ),
        np.ones(512, dtype=np.float32),
    )

    assert TestClient(main.app).get("/thumbnail/stale").status_code == 404


def test_cors_allowed_origins_configurable_via_env(tmp_path, monkeypatch):
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "http://192.168.1.50:5173,http://localhost:5173")
    main, _ = _fresh_app(tmp_path, monkeypatch)
    assert main.config.CORS_ALLOWED_ORIGINS == [
        "http://192.168.1.50:5173", "http://localhost:5173"
    ]

    # Exercise the actual CORSMiddleware wiring, not just the config value —
    # this would still pass even if main.py's CORSMiddleware line reverted to
    # a hardcoded origin list, since it never touches the config value above.
    client = TestClient(main.app)
    response = client.get("/health", headers={"Origin": "http://192.168.1.50:5173"})
    assert response.headers["access-control-allow-origin"] == "http://192.168.1.50:5173"


def test_download_endpoint_returns_original_file_as_attachment(tmp_path, monkeypatch):
    main, _ = _fresh_app(tmp_path, monkeypatch)
    from app.storage import ImageEntry

    original = tmp_path / "original.png"
    original.write_bytes(b"fake-png-bytes")
    entry = ImageEntry(
        id="d1", path=str(original), thumbnail_path=str(tmp_path / "d1.jpg"),
        ocr_text="", colors=[], objects=[], mtime=0.0, size=0,
    )
    main.store.upsert(entry, np.ones(512, dtype=np.float32))

    client = TestClient(main.app)
    response = client.get("/download/d1")

    assert response.status_code == 200
    assert response.content == b"fake-png-bytes"
    assert "attachment" in response.headers["content-disposition"]
    assert "original.png" in response.headers["content-disposition"]


def test_download_endpoint_404_for_unknown_id(tmp_path, monkeypatch):
    main, _ = _fresh_app(tmp_path, monkeypatch)
    client = TestClient(main.app)
    assert client.get("/download/missing").status_code == 404


def test_download_endpoint_404_when_original_was_deleted(tmp_path, monkeypatch):
    main, _ = _fresh_app(tmp_path, monkeypatch)
    from app.storage import ImageEntry

    main.store.upsert(
        ImageEntry(
            id="stale", path=str(tmp_path / "gone.png"),
            thumbnail_path=str(tmp_path / "stale.jpg"), ocr_text="",
            colors=[], objects=[], mtime=0.0, size=0,
        ),
        np.ones(512, dtype=np.float32),
    )

    assert TestClient(main.app).get("/download/stale").status_code == 404


def test_model_status_reports_not_installed_when_checkpoint_missing(tmp_path, monkeypatch):
    main, _ = _fresh_app(tmp_path, monkeypatch)
    monkeypatch.setattr(main.config, "RAM_CHECKPOINT_PATH", tmp_path / "does-not-exist.pth")
    client = TestClient(main.app)
    assert client.get("/model/status").json() == {"installed": False}


def test_model_status_reports_installed_when_checkpoint_present(tmp_path, monkeypatch):
    main, _ = _fresh_app(tmp_path, monkeypatch)
    checkpoint = tmp_path / "ram_plus.pth"
    checkpoint.write_bytes(b"fake-checkpoint")
    monkeypatch.setattr(main.config, "RAM_CHECKPOINT_PATH", checkpoint)
    client = TestClient(main.app)
    assert client.get("/model/status").json() == {"installed": True}


def test_model_download_returns_409_when_already_installed(tmp_path, monkeypatch):
    main, _ = _fresh_app(tmp_path, monkeypatch)
    checkpoint = tmp_path / "ram_plus.pth"
    checkpoint.write_bytes(b"fake-checkpoint")
    monkeypatch.setattr(main.config, "RAM_CHECKPOINT_PATH", checkpoint)
    client = TestClient(main.app)
    assert client.post("/model/download").status_code == 409


def test_model_download_reports_progress_until_done(tmp_path, monkeypatch):
    main, _ = _fresh_app(tmp_path, monkeypatch)
    monkeypatch.setattr(main.config, "RAM_CHECKPOINT_PATH", tmp_path / "ram_plus.pth")
    release = threading.Event()

    def fake_run_download(job):
        job.total_bytes = 100
        job.downloaded_bytes = 40
        release.wait(timeout=5)
        job.downloaded_bytes = 100
        job.done = True

    monkeypatch.setattr(main, "run_download", fake_run_download)
    client = TestClient(main.app)

    job_id = client.post("/model/download").json()["job_id"]
    try:
        mid = client.get(f"/model/download/status/{job_id}").json()
        assert mid["done"] is False
        assert mid["downloaded_bytes"] == 40
        assert mid["total_bytes"] == 100
    finally:
        release.set()

    for _ in range(40):
        final = client.get(f"/model/download/status/{job_id}").json()
        if final["done"]:
            break
        time.sleep(0.05)

    assert final == {
        "downloaded_bytes": 100, "total_bytes": 100, "done": True, "error": None, "cancelled": False,
    }


def test_second_model_download_while_one_is_running_returns_409(tmp_path, monkeypatch):
    main, _ = _fresh_app(tmp_path, monkeypatch)
    monkeypatch.setattr(main.config, "RAM_CHECKPOINT_PATH", tmp_path / "ram_plus.pth")
    release = threading.Event()

    def fake_run_download(job):
        release.wait(timeout=5)
        job.done = True

    monkeypatch.setattr(main, "run_download", fake_run_download)
    client = TestClient(main.app)

    first = client.post("/model/download")
    assert first.status_code == 200
    try:
        second = client.post("/model/download")
        assert second.status_code == 409
    finally:
        release.set()


def test_cancel_model_download_sets_the_job_cancel_event(tmp_path, monkeypatch):
    main, _ = _fresh_app(tmp_path, monkeypatch)
    monkeypatch.setattr(main.config, "RAM_CHECKPOINT_PATH", tmp_path / "ram_plus.pth")
    release = threading.Event()

    def fake_run_download(job):
        release.wait(timeout=5)
        job.done = True

    monkeypatch.setattr(main, "run_download", fake_run_download)
    client = TestClient(main.app)

    job_id = client.post("/model/download").json()["job_id"]
    try:
        response = client.post(f"/model/download/{job_id}/cancel")
        assert response.status_code == 200
        assert main.model_download_jobs[job_id].cancel_event.is_set()
    finally:
        release.set()


def test_cancel_model_download_returns_404_for_unknown_job(tmp_path, monkeypatch):
    main, _ = _fresh_app(tmp_path, monkeypatch)
    client = TestClient(main.app)
    assert client.post("/model/download/does-not-exist/cancel").status_code == 404


def test_cancel_model_download_returns_409_for_an_already_finished_job(tmp_path, monkeypatch):
    main, _ = _fresh_app(tmp_path, monkeypatch)
    monkeypatch.setattr(main.config, "RAM_CHECKPOINT_PATH", tmp_path / "ram_plus.pth")

    def instant_run_download(job):
        job.done = True

    monkeypatch.setattr(main, "run_download", instant_run_download)
    client = TestClient(main.app)

    job_id = client.post("/model/download").json()["job_id"]
    for _ in range(40):
        status = client.get(f"/model/download/status/{job_id}").json()
        if status["done"]:
            break
        time.sleep(0.05)

    assert client.post(f"/model/download/{job_id}/cancel").status_code == 409
