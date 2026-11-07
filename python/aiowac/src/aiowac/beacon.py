"""Parsing of the WAC vendor information element in an accessory's soft-AP beacon."""

from __future__ import annotations

from dataclasses import dataclass, field

from . import tlv
from .exceptions import WacProtocolError

VENDOR_IE_ID = 0xDD
WAC_OUI = bytes.fromhex("00a040")
WAC_OUI_TYPE = 0x00
WAC_PREFIX = WAC_OUI + bytes((WAC_OUI_TYPE,))

_FLAGS = 0x00
_NAME = 0x01
_MANUFACTURER = 0x02
_MODEL = 0x03
_DEVICE_ID = 0x07
_KNOWN = {_FLAGS, _NAME, _MANUFACTURER, _MODEL, _DEVICE_ID}


@dataclass(frozen=True)
class WacBeacon:
    device_id: str
    flags: bytes = b""
    name: str = ""
    manufacturer: str = ""
    model: str = ""
    extra: dict[int, bytes] = field(default_factory=dict)


def parse_payload(payload: bytes) -> WacBeacon:
    """Parse the TLV8 payload that follows `00 A0 40 00` in the vendor IE."""
    fields = tlv.decode_dict(payload)
    raw_id = fields.get(_DEVICE_ID)
    if raw_id is None or len(raw_id) != 6:
        raise WacProtocolError("WAC beacon has no 6-byte device ID")
    return WacBeacon(
        device_id=":".join(f"{b:02X}" for b in raw_id),
        flags=fields.get(_FLAGS, b""),
        name=_text(fields.get(_NAME)),
        manufacturer=_text(fields.get(_MANUFACTURER)),
        model=_text(fields.get(_MODEL)),
        extra={k: v for k, v in fields.items() if k not in _KNOWN},
    )


def find_in_ies(ies: bytes) -> WacBeacon | None:
    """Find and parse the WAC element in a raw 802.11 information-element list.

    Returns None when there is no WAC element. Raises WacProtocolError when the list or
    the WAC element is malformed.
    """
    pos = 0
    while pos < len(ies):
        if len(ies) - pos < 2:
            raise WacProtocolError("malformed IE list")
        ie_id, length = ies[pos], ies[pos + 1]
        body = ies[pos + 2 : pos + 2 + length]
        if len(body) != length:
            raise WacProtocolError("truncated IE")
        if ie_id == VENDOR_IE_ID and body.startswith(WAC_PREFIX):
            return parse_payload(body[len(WAC_PREFIX) :])
        pos += 2 + length
    return None


def _text(value: bytes | None) -> str:
    return value.decode("utf-8", errors="replace") if value else ""
