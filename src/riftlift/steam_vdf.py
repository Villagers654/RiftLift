from __future__ import annotations

import struct
from collections.abc import Mapping
from typing import Any


TYPE_OBJECT = 0x00
TYPE_STRING = 0x01
TYPE_INT32 = 0x02
TYPE_END = 0x08


class VdfError(ValueError):
    pass


def _cstring(data: bytes, offset: int) -> tuple[str, int]:
    end = data.find(b"\0", offset)
    if end < 0:
        raise VdfError("unterminated VDF string")
    return data[offset:end].decode("utf-8", errors="surrogateescape"), end + 1


def loads(data: bytes) -> dict[str, Any]:
    def read_object(offset: int) -> tuple[dict[str, Any], int]:
        value: dict[str, Any] = {}
        while offset < len(data):
            kind = data[offset]
            offset += 1
            if kind == TYPE_END:
                return value, offset
            key, offset = _cstring(data, offset)
            if kind == TYPE_OBJECT:
                item, offset = read_object(offset)
            elif kind == TYPE_STRING:
                item, offset = _cstring(data, offset)
            elif kind == TYPE_INT32:
                if offset + 4 > len(data):
                    raise VdfError("truncated VDF integer")
                item = struct.unpack_from("<I", data, offset)[0]
                offset += 4
            else:
                raise VdfError(f"unsupported binary VDF type 0x{kind:02x}")
            value[key] = item
        raise VdfError("unterminated VDF object")

    result, offset = read_object(0)
    if offset != len(data):
        raise VdfError("trailing binary VDF data")
    return result


def dumps(value: Mapping[str, Any]) -> bytes:
    output = bytearray()

    def write_object(items: Mapping[str, Any]) -> None:
        for key, item in items.items():
            encoded_key = key.encode("utf-8", errors="surrogateescape") + b"\0"
            if isinstance(item, Mapping):
                output.append(TYPE_OBJECT)
                output.extend(encoded_key)
                write_object(item)
            elif isinstance(item, str):
                output.append(TYPE_STRING)
                output.extend(encoded_key)
                output.extend(item.encode("utf-8", errors="surrogateescape") + b"\0")
            elif isinstance(item, int):
                output.append(TYPE_INT32)
                output.extend(encoded_key)
                output.extend(struct.pack("<I", item & 0xFFFFFFFF))
            else:
                raise TypeError(f"cannot write {type(item).__name__} as binary VDF")
        output.append(TYPE_END)

    write_object(value)
    return bytes(output)
