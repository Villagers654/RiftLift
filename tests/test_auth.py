from pathlib import Path

from riftlift.auth import _tokens_in_leveldb_file


def test_reads_chromium_local_storage_token(tmp_path: Path) -> None:
    token = "FRL" + "a" * 176
    value = b"\x01" + token.encode()
    length = bytes(((len(value) & 0x7F) | 0x80, len(value) >> 7))
    target = tmp_path / "000003.log"
    target.write_bytes(
        b"record-prefix\x00\x01riftlift-token" + length + value + b"record-suffix"
    )
    assert _tokens_in_leveldb_file(target) == [token]
