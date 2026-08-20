import importlib

from fastapi.testclient import TestClient


def _secured_app(tmp_path, monkeypatch):
    images_dir = tmp_path / "images"
    images_dir.mkdir()
    monkeypatch.setenv("IMAGES_DIR", str(images_dir))
    monkeypatch.setenv("INDEX_DIR", str(tmp_path / "index"))
    monkeypatch.setenv("ENABLE_WATCHER", "false")
    from app import config, main
    importlib.reload(config)
    importlib.reload(main)
    return main


def test_protected_api_requires_a_session(tmp_path, monkeypatch):
    main = _secured_app(tmp_path, monkeypatch)
    client = TestClient(main.app, base_url="https://testserver")

    assert client.get("/health").status_code == 200
    response = client.get("/search")
    assert response.status_code == 401
    assert response.json() == {"detail": "authentication required"}
    assert response.headers["www-authenticate"] == "Session"


def test_production_login_shell_is_public_but_hardened(tmp_path, monkeypatch):
    main = _secured_app(tmp_path, monkeypatch)
    client = TestClient(main.app, base_url="https://testserver")

    response = client.get("/")

    assert response.status_code == 200
    assert "ImageFind" in response.text
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert "script-src 'self'" in response.headers["content-security-policy"]
    assert "'unsafe-eval'" not in response.headers["content-security-policy"]
    assert response.headers["strict-transport-security"].startswith("max-age=")


def test_login_fails_closed_until_password_is_configured(tmp_path, monkeypatch):
    main = _secured_app(tmp_path, monkeypatch)
    client = TestClient(main.app, base_url="https://testserver")

    assert client.get("/auth/session").json() == {
        "authenticated": False,
        "configured": False,
    }
    response = client.post("/auth/login", json={"password": "not-configured"})
    assert response.status_code == 503
    assert "auth:set-password" in response.json()["detail"]


def test_login_cookie_csrf_logout_and_password_rotation(tmp_path, monkeypatch):
    main = _secured_app(tmp_path, monkeypatch)
    main.auth_store.set_password("correct horse battery staple")
    client = TestClient(main.app, base_url="https://testserver")

    rejected = client.post("/auth/login", json={"password": "wrong password"})
    assert rejected.status_code == 401

    login = client.post("/auth/login", json={"password": "correct horse battery staple"})
    assert login.status_code == 200
    body = login.json()
    assert body["authenticated"] is True
    assert body["configured"] is True
    assert body["csrf_token"]
    cookie = login.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "Secure" in cookie
    assert "SameSite=strict" in cookie

    assert client.get("/search").status_code == 200
    assert client.post("/reindex").status_code == 403

    csrf_headers = {"x-csrf-token": body["csrf_token"]}
    reindex = client.post("/reindex", headers=csrf_headers)
    assert reindex.status_code == 200

    assert client.post("/auth/logout", headers=csrf_headers).status_code == 200
    assert client.get("/search").status_code == 401

    client.post("/auth/login", json={"password": "correct horse battery staple"})
    assert client.get("/search").status_code == 200
    main.auth_store.set_password("a different strong password")
    assert client.get("/search").status_code == 401


def test_login_rate_limit_is_per_client_ip(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTH_LOGIN_RATE_LIMIT_REQUESTS", "2")
    monkeypatch.setenv("AUTH_LOGIN_RATE_LIMIT_WINDOW_SECONDS", "300")
    main = _secured_app(tmp_path, monkeypatch)
    main.auth_store.set_password("correct horse battery staple")
    client = TestClient(main.app, base_url="https://testserver")

    for _ in range(2):
        assert client.post("/auth/login", json={"password": "wrong password"}).status_code == 401
    limited = client.post("/auth/login", json={"password": "correct horse battery staple"})
    assert limited.status_code == 429
    assert int(limited.headers["retry-after"]) >= 1


def test_login_rate_limit_has_a_global_window_across_client_ips(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTH_LOGIN_RATE_LIMIT_REQUESTS", "20")
    monkeypatch.setenv("AUTH_GLOBAL_LOGIN_RATE_LIMIT_REQUESTS", "2")
    monkeypatch.setenv("AUTH_GLOBAL_LOGIN_RATE_LIMIT_WINDOW_SECONDS", "300")
    main = _secured_app(tmp_path, monkeypatch)
    main.auth_store.set_password("correct horse battery staple")
    client = TestClient(main.app, base_url="https://testserver")

    for client_ip in ("203.0.113.1", "203.0.113.2"):
        response = client.post(
            "/auth/login",
            json={"password": "wrong password"},
            headers={"cf-connecting-ip": client_ip},
        )
        assert response.status_code == 401
    limited = client.post(
        "/auth/login",
        json={"password": "correct horse battery staple"},
        headers={"cf-connecting-ip": "203.0.113.3"},
    )
    assert limited.status_code == 429
    assert int(limited.headers["retry-after"]) >= 1


def test_auth_store_persists_sessions_as_token_hashes(tmp_path):
    from app.auth import AuthStore

    db_path = tmp_path / "auth.db"
    first = AuthStore(db_path, session_ttl_seconds=3600)
    first.set_password("correct horse battery staple")
    raw_token, session = first.create_session("correct horse battery staple", "127.0.0.1", "test")

    second = AuthStore(db_path, session_ttl_seconds=3600)
    assert second.get_session(raw_token) == session

    import sqlite3
    with sqlite3.connect(db_path) as connection:
        stored = connection.execute("SELECT token_hash FROM auth_sessions").fetchone()[0]
    assert stored != raw_token
    assert len(stored) == 64


def test_password_policy_rejects_short_passwords(tmp_path):
    from app.auth import AuthStore

    store = AuthStore(tmp_path / "auth.db", session_ttl_seconds=3600)
    try:
        store.set_password("too-short")
    except ValueError as exc:
        assert "at least 12" in str(exc)
    else:
        raise AssertionError("short password was accepted")
