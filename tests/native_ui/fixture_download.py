"""Standalone fixture worker: local files only, no account or network imports."""

import json
import os
import sys
import time
from pathlib import Path

request = json.loads(sys.stdin.readline())
cache = Path(request["paths"]["cache"])
data = Path(request["paths"]["data"])
cache.mkdir(parents=True, exist_ok=True)
(cache / "worker-pid").write_text(str(os.getpid()))
mode = (cache / "mode").read_text() if (cache / "mode").exists() else "pause"


def emit(event, **values):
    print(json.dumps({"event": event, **values}), flush=True)


if mode in {"sign_in_required", "download_failed"}:
    emit("error", reason=mode)
    raise SystemExit(1)
if mode == "crash":
    raise SystemExit(5)
if mode == "malformed":
    print("malformed worker event", flush=True)
    time.sleep(10)
    raise SystemExit(0)

completed = cache / "verified-segment"
resumed = completed.exists()
completed.write_bytes(b"fixture verified segment")
emit("progress", label="Downloading", current=1, total=2)
if mode == "pause" and not resumed:
    # Test pauses this real process after its completed segment is persisted.
    while True:
        time.sleep(0.01)
emit("progress", label="Downloading", current=2, total=2)
emit("finishing")
(cache / "awaiting-finalize").write_text("ready")
assert json.loads(sys.stdin.readline()) == {"event": "finalize"}
(data / "games").mkdir(parents=True, exist_ok=True)
(data / "games" / "fixture.json").write_text(
    json.dumps(
        {
            "slug": "fixture",
            "name": "Fixture game",
            "app_id": "123456789",
            "app_key": "meta.fixture",
            "directory": request["paths"]["games"],
            "executable": "fixture.exe",
            "arguments": [],
            "source": "meta",
        }
    )
)
if mode == "finishing":
    while not (cache / "release").exists():
        time.sleep(0.01)
if mode == "warning":
    emit("warning", reason="steam_sync_failed")
emit("complete", slug="fixture")
