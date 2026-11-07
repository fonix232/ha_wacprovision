"""The configurator side of a WAC session."""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Protocol

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from . import tlv
from .crypto import KeyStream, agree, public_bytes
from .exceptions import WacProtocolError, WacStateError

AUTH_SETUP_VERSION = 0x01
MAX_CERTIFICATE = 1280
MAX_SIGNATURE = 256

# /config request fields
_REQ_NAME = 0x08
_REQ_SSID = 0x0C
_REQ_PSK = 0x0B
_REQ_AIRPLAY_KEY = 0x09

# /config response fields
_RESP_BUNDLE_SEED = 0x01
_RESP_FIRMWARE = 0x02
_RESP_HARDWARE = 0x03
_RESP_MODEL = 0x07
_RESP_NAME = 0x08
_RESP_SERIAL = 0x0A
_RESP_KNOWN = {_RESP_BUNDLE_SEED, _RESP_FIRMWARE, _RESP_HARDWARE, _RESP_MODEL, _RESP_NAME, _RESP_SERIAL}


class Transport(Protocol):
    """Sends one POST to the accessory and returns the response body.

    Implementations raise WacHttpError for a non-200 status and must bound the body size.
    """

    async def post(self, path: str, body: bytes) -> bytes: ...


@dataclass(frozen=True)
class WifiConfig:
    name: str
    ssid: str
    psk: str | None = None
    airplay_key: str | None = None

    def __post_init__(self) -> None:
        if not 1 <= len(self.ssid.encode()) <= 32:
            raise ValueError("SSID must be 1 to 32 bytes")
        if self.psk is not None and not _valid_psk(self.psk):
            raise ValueError("PSK must be 8 to 63 characters, or 64 hex digits")
        if not self.name:
            raise ValueError("name must not be empty")

    def encode(self) -> bytes:
        items = [(_REQ_NAME, self.name.encode()), (_REQ_SSID, self.ssid.encode())]
        if self.psk is not None:
            items.append((_REQ_PSK, self.psk.encode()))
        if self.airplay_key:
            items.append((_REQ_AIRPLAY_KEY, self.airplay_key.encode()))
        return tlv.encode(items)

    def __repr__(self) -> str:  # keep secrets out of logs and tracebacks
        return f"WifiConfig(name={self.name!r}, ssid={self.ssid!r}, psk={'***' if self.psk else None})"


@dataclass(frozen=True)
class AuthSetup:
    accessory_public_key: bytes
    certificate: bytes
    signature: bytes


@dataclass(frozen=True)
class AccessoryInfo:
    name: str = ""
    model: str = ""
    serial_number: str = ""
    firmware_version: str = ""
    hardware_version: str = ""
    bundle_seed: str = ""
    extra: dict[int, bytes] = field(default_factory=dict)

    @classmethod
    def decode(cls, data: bytes) -> AccessoryInfo:
        fields = tlv.decode_dict(data)

        def text(key: int) -> str:
            return fields[key].decode("utf-8", errors="replace") if key in fields else ""

        return cls(
            name=text(_RESP_NAME),
            model=text(_RESP_MODEL),
            serial_number=text(_RESP_SERIAL),
            firmware_version=text(_RESP_FIRMWARE),
            hardware_version=text(_RESP_HARDWARE),
            bundle_seed=text(_RESP_BUNDLE_SEED),
            extra={k: v for k, v in fields.items() if k not in _RESP_KNOWN},
        )


@dataclass(frozen=True)
class ProvisionResult:
    auth: AuthSetup
    info: AccessoryInfo | None  # None when the accessory sent an empty /config response


class WacSession:
    """One configurator session on the accessory's soft-AP: auth-setup, then config.

    The accessory's MFi signature is not verified yet (see AuthSetup.certificate).
    """

    def __init__(self, transport: Transport, private_key: X25519PrivateKey | None = None) -> None:
        self._transport = transport
        self._private_key = private_key or X25519PrivateKey.generate()
        self._stream: KeyStream | None = None
        self._configured = False

    async def auth_setup(self) -> AuthSetup:
        if self._stream is not None:
            raise WacStateError("auth-setup already done")
        client_public = public_bytes(self._private_key)
        response = await self._transport.post("auth-setup", bytes((AUTH_SETUP_VERSION,)) + client_public)
        accessory_public, certificate, encrypted_signature = _parse_auth_setup(response)
        self._stream = agree(self._private_key, accessory_public)
        signature = self._stream.apply(encrypted_signature)
        return AuthSetup(accessory_public, certificate, signature)

    async def configure(self, config: WifiConfig) -> AccessoryInfo | None:
        if self._stream is None:
            raise WacStateError("auth-setup must come before config")
        if self._configured:
            raise WacStateError("config already sent in this session")
        self._configured = True
        response = await self._transport.post("config", self._stream.apply(config.encode()))
        if not response:
            return None
        return AccessoryInfo.decode(self._stream.apply(response))


async def provision(transport: Transport, config: WifiConfig) -> ProvisionResult:
    """Run auth-setup and config against an accessory reachable through `transport`."""
    session = WacSession(transport)
    auth = await session.auth_setup()
    info = await session.configure(config)
    return ProvisionResult(auth, info)


async def confirm_configured(transport: Transport) -> None:
    """Tell the accessory, now on the target network, that configuration succeeded."""
    await transport.post("configured", b"")


def _parse_auth_setup(data: bytes) -> tuple[bytes, bytes, bytes]:
    if len(data) < 32:
        raise WacProtocolError("auth-setup response shorter than a public key")
    public_key, rest = data[:32], data[32:]
    certificate, rest = _take_len32(rest, MAX_CERTIFICATE, "certificate")
    signature, rest = _take_len32(rest, MAX_SIGNATURE, "signature")
    if rest:
        raise WacProtocolError("trailing data after auth-setup signature")
    return public_key, certificate, signature


def _take_len32(data: bytes, limit: int, what: str) -> tuple[bytes, bytes]:
    if len(data) < 4:
        raise WacProtocolError(f"auth-setup response missing {what} length")
    (length,) = struct.unpack(">I", data[:4])
    if length > limit or length > len(data) - 4:
        raise WacProtocolError(f"auth-setup {what} length {length} invalid")
    return data[4 : 4 + length], data[4 + length :]


def _valid_psk(psk: str) -> bool:
    if len(psk) == 64:
        return all(c in "0123456789abcdefABCDEF" for c in psk)
    return 8 <= len(psk) <= 63
