from app import config
from app.model_download import ModelDownloadJob, is_ram_checkpoint_installed, run_download


class _FakeResponse:
    def __init__(self, chunks: list[bytes], status_code: int = 200, content_length: int | None = None):
        self._chunks = chunks
        self.status_code = status_code
        expected = sum(len(c) for c in chunks) if content_length is None else content_length
        self.headers = {"content-length": str(expected)}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"status {self.status_code}")

    def iter_content(self, chunk_size):
        yield from self._chunks

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_is_ram_checkpoint_installed_reflects_file_presence(tmp_path, monkeypatch):
    checkpoint = tmp_path / "ram_plus.pth"
    monkeypatch.setattr(config, "RAM_CHECKPOINT_PATH", checkpoint)
    assert is_ram_checkpoint_installed() is False
    checkpoint.write_bytes(b"data")
    assert is_ram_checkpoint_installed() is True


def test_run_download_writes_checkpoint_and_marks_done(tmp_path, monkeypatch):
    dest = tmp_path / "models" / "ram_plus.pth"
    monkeypatch.setattr(config, "RAM_CHECKPOINT_PATH", dest)
    monkeypatch.setattr(
        "app.model_download.requests.get",
        lambda *a, **k: _FakeResponse([b"abc", b"defg"]),
    )

    job = ModelDownloadJob(id="j1")
    run_download(job)

    assert dest.read_bytes() == b"abcdefg"
    assert job.done is True
    assert job.error is None
    assert job.cancelled is False
    assert job.downloaded_bytes == 7
    assert job.total_bytes == 7
    # The .part scratch file must not be left behind once the rename succeeds.
    assert not dest.with_suffix(dest.suffix + ".part").exists()


def test_run_download_cancellation_removes_partial_file(tmp_path, monkeypatch):
    dest = tmp_path / "ram_plus.pth"
    monkeypatch.setattr(config, "RAM_CHECKPOINT_PATH", dest)
    monkeypatch.setattr(
        "app.model_download.requests.get",
        lambda *a, **k: _FakeResponse([b"a" * 10, b"b" * 10, b"c" * 10]),
    )

    job = ModelDownloadJob(id="j2")
    job.cancel_event.set()
    run_download(job)

    assert job.done is True
    assert job.cancelled is True
    assert not dest.exists()
    assert not dest.with_suffix(dest.suffix + ".part").exists()


def test_run_download_records_error_and_cleans_up_on_failure(tmp_path, monkeypatch):
    dest = tmp_path / "ram_plus.pth"
    monkeypatch.setattr(config, "RAM_CHECKPOINT_PATH", dest)

    def _boom(*a, **k):
        raise ConnectionError("network unreachable")

    monkeypatch.setattr("app.model_download.requests.get", _boom)

    job = ModelDownloadJob(id="j3")
    run_download(job)

    assert job.done is True
    assert job.error is not None
    assert "network unreachable" in job.error
    assert not dest.exists()
    assert not dest.with_suffix(dest.suffix + ".part").exists()


def test_run_download_rejects_a_truncated_response(tmp_path, monkeypatch):
    dest = tmp_path / "ram_plus.pth"
    monkeypatch.setattr(config, "RAM_CHECKPOINT_PATH", dest)
    monkeypatch.setattr(
        "app.model_download.requests.get",
        lambda *a, **k: _FakeResponse([b"partial"], content_length=100),
    )

    job = ModelDownloadJob(id="j4")
    run_download(job)

    assert job.done is True
    assert job.error is not None and "incomplete download" in job.error
    assert not dest.exists()
    assert not dest.with_suffix(dest.suffix + ".part").exists()
