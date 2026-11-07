"""Apple TLV8 encoding, as used by WAC.

Values longer than 255 bytes are split into consecutive fragments of the same type; every
fragment except the last is exactly 255 bytes long. Decoding merges them back.
"""

from __future__ import annotations

from collections.abc import Iterable

from .exceptions import WacProtocolError

MAX_FRAGMENT = 255


def encode(items: Iterable[tuple[int, bytes]]) -> bytes:
    out = bytearray()
    for item_type, value in items:
        if not 0 <= item_type <= 0xFF:
            raise ValueError(f"TLV type out of range: {item_type}")
        if not value:
            out += bytes((item_type, 0))
            continue
        for start in range(0, len(value), MAX_FRAGMENT):
            chunk = value[start : start + MAX_FRAGMENT]
            out += bytes((item_type, len(chunk)))
            out += chunk
    return bytes(out)


def decode(data: bytes) -> list[tuple[int, bytes]]:
    items: list[tuple[int, bytearray]] = []
    continues = False
    pos = 0
    while pos < len(data):
        if len(data) - pos < 2:
            raise WacProtocolError("malformed TLV: dangling type byte")
        item_type, length = data[pos], data[pos + 1]
        pos += 2
        if length > len(data) - pos:
            raise WacProtocolError(f"malformed TLV: type 0x{item_type:02x} truncated")
        value = data[pos : pos + length]
        pos += length
        if continues and items and items[-1][0] == item_type:
            items[-1][1].extend(value)
        else:
            items.append((item_type, bytearray(value)))
        continues = length == MAX_FRAGMENT
    return [(item_type, bytes(value)) for item_type, value in items]


def decode_dict(data: bytes) -> dict[int, bytes]:
    """Decode into a mapping; the last value wins for a repeated type."""
    return dict(decode(data))
