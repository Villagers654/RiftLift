from io import BytesIO

import pytest

from riftlift.util import RiftLiftError, download, read_limited


class Response(BytesIO):
    def __init__(self, payload: bytes, content_length: str | None = None):
        super().__init__(payload)
        self.headers = {"Content-Length": content_length} if content_length else {}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def test_limited_read_rejects_declared_or_actual_oversize():
    with pytest.raises(RiftLiftError, match="2 MiB limit"):
        read_limited(Response(b"small", "3000000"), 2 * 1024 * 1024, "catalog")
    with pytest.raises(RiftLiftError, match="2 MiB limit"):
        read_limited(
            Response(b"x" * (2 * 1024 * 1024 + 1)),
            2 * 1024 * 1024,
            "catalog",
        )


def test_limited_read_accepts_payload_at_limit():
    payload = b"x" * 1024
    assert read_limited(Response(payload), len(payload), "catalog") == payload


def test_download_retries_without_retaining_partial_bytes(tmp_path, monkeypatch):
    attempts = []

    def urlopen(_request, timeout):
        attempts.append(timeout)
        if len(attempts) < 3:
            response = Response(b"partial")
            response.read = lambda *_args: (_ for _ in ()).throw(
                ConnectionError("lost")
            )
            return response
        return Response(b"complete")

    monkeypatch.setattr("riftlift.util.urllib.request.urlopen", urlopen)
    monkeypatch.setattr("riftlift.util.time.sleep", lambda _seconds: None)

    target = download("https://example.invalid/file", tmp_path / "file")

    assert target.read_bytes() == b"complete"
    assert attempts == [60, 60, 60]


def test_download_reports_exhausted_retries(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "riftlift.util.urllib.request.urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ConnectionError("offline")),
    )
    monkeypatch.setattr("riftlift.util.time.sleep", lambda _seconds: None)

    with pytest.raises(RiftLiftError, match="after 4 attempts: offline"):
        download("https://example.invalid/file", tmp_path / "file")
