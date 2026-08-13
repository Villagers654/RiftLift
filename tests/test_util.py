from io import BytesIO

import pytest

from riftlift.util import RiftLiftError, download


class Response(BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


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
