"""protobuf wire-format デコーダの単体テスト。"""

import pytest

import main


def _enc_varint(v: int) -> bytes:
    out = bytearray()
    while v > 0x7f:
        out.append((v & 0x7f) | 0x80)
        v >>= 7
    out.append(v & 0x7f)
    return bytes(out)


def _enc_string(field: int, value: str) -> bytes:
    body = value.encode("utf-8")
    return _enc_varint((field << 3) | 2) + _enc_varint(len(body)) + body


def _enc_varint_field(field: int, v: int) -> bytes:
    return _enc_varint((field << 3) | 0) + _enc_varint(v)


def _enc_message(field: int, body: bytes) -> bytes:
    return _enc_varint((field << 3) | 2) + _enc_varint(len(body)) + body


def test_decodes_varint_field() -> None:
    buf = _enc_varint_field(1, 12345) + _enc_varint_field(7, 0)
    assert main.proto_decode(buf) == [(1, "varint", 12345), (7, "varint", 0)]


def test_decodes_string_field() -> None:
    buf = _enc_string(2, "日本三國")
    assert main.proto_decode(buf) == [(2, "str", "日本三國")]


def test_empty_length_delimited_decodes_as_empty_string() -> None:
    """field 16 が空 → 空文字列として現れる（無料章を表す印）。"""
    buf = _enc_varint((16 << 3) | 2) + _enc_varint(0)
    assert main.proto_decode(buf) == [(16, "str", "")]


def test_decodes_nested_message() -> None:
    inner = _enc_varint_field(1, 1) + _enc_varint_field(2, 1)
    buf = _enc_message(16, inner)
    decoded = main.proto_decode(buf)
    assert len(decoded) == 1
    field, wire, value = decoded[0]
    assert field == 16
    assert wire == "msg"
    assert value == [(1, "varint", 1), (2, "varint", 1)]


def test_returns_none_on_truncated_buffer() -> None:
    buf = _enc_varint((1 << 3) | 2) + _enc_varint(100) + b"short"
    assert main.proto_decode(buf) is None
