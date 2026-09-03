"""Opt-in SteamVR null headset for desktop rendering tests on Windows."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from .config import Paths
from .util import RiftLiftError, atomic_write_text


def configure(paths: Paths, runtime: Path) -> dict[str, str]:
    runtime = runtime.expanduser().resolve()
    required = (
        "bin/win64/vrstartup.exe",
        "bin/vrclient_x64.dll",
        "drivers/null/bin/win64/driver_null.dll",
        "steamxr_win64.json",
    )
    if not all((runtime / name).is_file() for name in required):
        raise RiftLiftError(
            "Choose a complete Windows SteamVR installation with its null driver"
        )
    root = paths.data / "simulator"
    config, logs = root / "config", root / "logs"
    config.mkdir(parents=True, exist_ok=True)
    logs.mkdir(parents=True, exist_ok=True)
    registry = root / "openvrpaths.vrpath"
    atomic_write_text(
        registry,
        json.dumps(
            {
                "jsonid": "vrpathreg",
                "version": 1,
                "runtime": [str(runtime)],
                "config": [str(config)],
                "log": [str(logs)],
                "external_drivers": [],
            },
            indent=2,
        ),
    )
    settings = config / "steamvr.vrsettings"
    if not settings.exists():
        atomic_write_text(
            settings,
            json.dumps(
                {
                    "steamvr": {
                        "forcedDriver": "null",
                        "activateMultipleDrivers": True,
                        "enableHomeApp": False,
                        "showMirrorView": True,
                        "renderTargetMultiplier": 1.0,
                    },
                    "driver_null": {
                        "enable": True,
                        "serialNumber": "RiftLift Simulated HMD",
                        "modelNumber": "RiftLift desktop test headset",
                        "windowX": 100,
                        "windowY": 100,
                        "windowWidth": 1280,
                        "windowHeight": 720,
                        "renderWidth": 1024,
                        "renderHeight": 1024,
                        "displayFrequency": 90.0,
                    },
                    "power": {
                        "pauseCompositorOnStandby": False,
                        "turnOffScreensTimeout": 86400.0,
                    },
                },
                indent=2,
            ),
        )
    return {
        "VR_PATHREG_OVERRIDE": str(registry),
        "XR_RUNTIME_JSON": str(runtime / "steamxr_win64.json"),
        "RIFTLIFT_SIMULATOR": "1",
        "RIFTLIFT_SIMULATED_CONTROLLERS": "1",
    }


def start(paths: Paths, runtime: Path) -> None:
    os.environ.update(configure(paths, runtime))
    subprocess.Popen(
        [str(runtime.resolve() / "bin/win64/vrstartup.exe")],
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    print(
        "SteamVR simulated headset and static hand poses started. Physical tracking/controllers/audio are not verified."
    )
