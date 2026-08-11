from __future__ import annotations

import os
import selectors
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .util import RiftLiftError

PROTOCOL_VERSION = 2


@dataclass(frozen=True, slots=True)
class RuntimeEndpoint:
    backend: str
    host: str
    port: int
    token: str
    runtime_name: str
    runtime_version: int

    def environment(self) -> dict[str, str]:
        return {
            "RIFTLIFT_RUNTIME_PROTOCOL": str(PROTOCOL_VERSION),
            "RIFTLIFT_RUNTIME_ENDPOINT": f"{self.host}:{self.port}",
            "RIFTLIFT_RUNTIME_TOKEN": self.token,
        }


def parse_ready(line: str) -> RuntimeEndpoint:
    fields = line.rstrip("\r\n").split("\t", 7)
    if len(fields) != 8 or fields[0] != "RIFTLIFT_RUNTIME":
        raise RiftLiftError("native runtime returned an invalid readiness message")
    try:
        protocol = int(fields[1])
        port = int(fields[4])
        runtime_version = int(fields[6])
    except ValueError as error:
        raise RiftLiftError(
            "native runtime returned invalid endpoint values"
        ) from error
    if protocol != PROTOCOL_VERSION:
        raise RiftLiftError(
            f"native runtime protocol {protocol} is incompatible with RiftLift "
            f"protocol {PROTOCOL_VERSION}"
        )
    if (
        fields[2] not in {"openxr", "openvr"}
        or fields[3] != "127.0.0.1"
        or not 0 < port < 65536
        or not fields[5]
    ):
        raise RiftLiftError("native runtime returned an unsafe endpoint")
    return RuntimeEndpoint(
        fields[2], fields[3], port, fields[5], fields[7], runtime_version
    )


def _read_ready(process: subprocess.Popen[str], timeout: float) -> RuntimeEndpoint:
    if process.stdout is None:
        raise RiftLiftError("native runtime output is unavailable")
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    try:
        if not selector.select(timeout):
            raise RiftLiftError("native runtime did not become ready in time")
        line = process.stdout.readline()
    finally:
        selector.close()
    if not line:
        detail = process.stderr.read().strip() if process.stderr else ""
        raise RiftLiftError(
            "native runtime stopped during startup" + (f": {detail}" if detail else "")
        )
    return parse_ready(line)


class NativeRuntimeHost:
    def __init__(
        self,
        process: subprocess.Popen[str],
        endpoint: RuntimeEndpoint,
    ) -> None:
        self.process = process
        self.endpoint = endpoint

    @classmethod
    def start(
        cls,
        executable: Path,
        environment: dict[str, str],
        backend: str,
        *,
        timeout: float = 8.0,
    ) -> "NativeRuntimeHost":
        if not executable.is_file() or not os.access(executable, os.X_OK):
            raise RiftLiftError(f"native RiftLift runtime is missing: {executable}")
        process = subprocess.Popen(
            [str(executable), f"--backend={backend}"],
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            endpoint = _read_ready(process, timeout)
        except BaseException:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            raise
        return cls(process, endpoint)

    def close(self) -> None:
        if self.process.poll() is not None:
            return
        try:
            with socket.create_connection(
                (self.endpoint.host, self.endpoint.port), timeout=1
            ) as connection:
                connection.sendall(f"SHUTDOWN {self.endpoint.token}\n".encode("ascii"))
                connection.recv(128)
            self.process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()

    def __enter__(self) -> "NativeRuntimeHost":
        return self

    def __exit__(self, *_error: object) -> None:
        self.close()
