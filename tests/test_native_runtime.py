import pytest

from riftlift.native_runtime import PROTOCOL_VERSION, parse_ready
from riftlift.util import RiftLiftError


def test_native_runtime_readiness_message() -> None:
    endpoint = parse_ready(
        "RIFTLIFT_RUNTIME\t2\topenxr\t127.0.0.1\t32100\tsecret\t1234\tMonado(XRT)\n"
    )

    assert endpoint.backend == "openxr"
    assert endpoint.host == "127.0.0.1"
    assert endpoint.port == 32100
    assert endpoint.runtime_name == "Monado(XRT)"
    assert endpoint.runtime_version == 1234
    assert endpoint.environment() == {
        "RIFTLIFT_RUNTIME_PROTOCOL": str(PROTOCOL_VERSION),
        "RIFTLIFT_RUNTIME_ENDPOINT": "127.0.0.1:32100",
        "RIFTLIFT_RUNTIME_TOKEN": "secret",
    }


@pytest.mark.parametrize(
    "message",
    (
        "garbage",
        "RIFTLIFT_RUNTIME\t1\topenxr\t127.0.0.1\t32100\tsecret\t1234\tMonado",
        "RIFTLIFT_RUNTIME\t2\topenxr\t0.0.0.0\t32100\tsecret\t1234\tMonado",
        "RIFTLIFT_RUNTIME\t2\topenxr\t127.0.0.1\tnope\tsecret\t1234\tMonado",
    ),
)
def test_native_runtime_rejects_invalid_readiness(message: str) -> None:
    with pytest.raises(RiftLiftError):
        parse_ready(message)
