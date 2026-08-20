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


def _quoted_text_token(value: str, offset: int) -> tuple[str, int]:
    token = []
    while offset < len(value) and value[offset] != '"':
        if value[offset] == "\\" and offset + 1 < len(value):
            offset += 1
            escaped = value[offset]
            if escaped not in {'"', "\\"}:
                token.append("\\")
        token.append(value[offset])
        offset += 1
    if offset >= len(value):
        raise VdfError("unterminated VDF string")
    return "".join(token), offset + 1


def _text_tokens(value: str) -> list[str]:
    tokens: list[str] = []
    offset = 0
    while offset < len(value):
        character = value[offset]
        if character.isspace():
            offset += 1
            continue
        if value.startswith("//", offset):
            newline = value.find("\n", offset + 2)
            offset = len(value) if newline < 0 else newline + 1
            continue
        if character in "{}":
            tokens.append(character)
            offset += 1
            continue
        if character == '"':
            token, offset = _quoted_text_token(value, offset + 1)
            tokens.append(token)
            continue
        end = offset
        while end < len(value) and not value[end].isspace() and value[end] not in "{}":
            end += 1
        tokens.append(value[offset:end])
        offset = end
    return tokens


def _read_text_object(
    tokens: list[str], index: int, nested: bool = False
) -> tuple[dict[str, Any], int]:
    result: dict[str, Any] = {}
    while index < len(tokens):
        key = tokens[index]
        index += 1
        if key == "}":
            if not nested:
                raise VdfError("unexpected closing brace")
            return result, index
        if key == "{":
            raise VdfError("missing VDF object key")
        if index >= len(tokens) or tokens[index] == "}":
            raise VdfError(f"missing value for VDF key {key!r}")
        if tokens[index] == "{":
            item, index = _read_text_object(tokens, index + 1, True)
        else:
            item = tokens[index]
            index += 1
        result[key] = item
    if nested:
        raise VdfError("unterminated VDF object")
    return result, index


def loads_text(value: str) -> dict[str, Any]:
    """Parse the KeyValues text format used by Steam configuration."""
    result, _offset = _read_text_object(_text_tokens(value), 0)
    return result


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
