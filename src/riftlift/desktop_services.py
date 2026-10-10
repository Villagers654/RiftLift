"""Lazy desktop service boundary; importing widgets never loads a VR runtime.

Linux delegates to the existing services. Native Windows packaging supplies the
existing windows/windows_ui_backend modules maintained on windows-native.
"""

import os

from .util import RiftLiftError


def _windows():
    try:
        from . import windows
    except ImportError as error:
        raise RiftLiftError(
            "The native Windows backend is not included in this checkout."
        ) from error
    return windows


def supports_steam_import():
    return os.name != "nt"


def supports_steam_shortcuts():
    return os.name != "nt"


def needs_setup(paths):
    if os.name == "nt":
        backend = _windows()
        return not all(
            (backend.runtime_dir(paths) / name).is_file()
            for name in backend.FILES | backend.PLATFORM_FILES
        )
    from .doctor_components import needs_setup as check

    return check(paths)


def setup(paths):
    if os.name == "nt":
        return _windows().install_payload(paths)
    from .runtime import setup as install

    return install(paths)


def doctor(paths, *, paste=True):
    if os.name == "nt":
        # Windows reports always stay local; there is nothing to upload.
        report, status = _windows().doctor(paths)
        print(report)
        if status:
            raise RiftLiftError("Windows VR setup needs attention; see View Activity.")
        return status
    from .doctor import doctor as check

    return check(paths, paste=paste)


def active_runtime_json():
    if os.name == "nt":
        backend = _windows()
        path = backend.active_openxr() or backend.active_openvr()
        if path is None:
            raise RiftLiftError("No active Windows VR runtime was found.")
        return path
    from .xr_runtime import active_runtime_json as active

    return active()


def launch(paths, game, arguments):
    if os.name == "nt":
        from .windows_ui_backend import launch as run

        return run(paths, game, arguments)
    from .launch import launch as run

    return run(paths, game, arguments)


def add_local(paths, executable, *, name=None, arguments=None, artwork=None):
    if os.name == "nt":
        return _windows().add_local(
            paths, executable, name=name, arguments=arguments, artwork=artwork
        )
    from .library import add_local as register

    return register(paths, executable, name=name, arguments=arguments, artwork=artwork)


def running_launch(paths):
    if os.name == "nt":
        # The native branch tracks a launch in the owning task. Linux also has
        # a persistent external-launch record (Steam/headset/CLI entry points).
        return None
    from .launch import running_launch as running

    return running(paths)
