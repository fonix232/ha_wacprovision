"""A fake WAC accessory (soft-AP side), written independently of aiowac's own code.

It mirrors the reference server in BertoldVdb/WACResearch: one AES-128-CTR stream per
session, used for the signature, then the /config request, then the /config response.
Crypto and TLV handling are deliberately reimplemented here rather than imported, so a
bug in aiowac cannot cancel itself out.
"""

from __future__ import annotations

import hashlib
import os
import struct
from dataclasses import dataclass, field

from aiohttp import web
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat


def simple_tlv_encode(items: list[tuple[int, bytes]]) -> bytes:
    out = b""
    for t, v in items:
        assert len(v) <= 255
        out += bytes((t, len(v))) + v
    return out


def simple_tlv_decode(data: bytes) -> dict[int, bytes]:
    out: dict[int, bytes] = {}
    pos = 0
    while pos < len(data):
        t, n = data[pos], data[pos + 1]
        out[t] = out.get(t, b"") + data[pos + 2 : pos + 2 + n]
        pos += 2 + n
    return out


@dataclass
class FakeAccessory:
    certificate: bytes = b"\x30\x82fake-pkcs7"
    signature: bytes = field(default_factory=lambda: os.urandom(128))
    response_fields: list[tuple[int, bytes]] = field(
        default_factory=lambda: [
            (0x08, b"Test Plug"),
            (0x07, b"TP-1"),
            (0x0A, b"SN0000000"),
            (0x02, b"1.2.3"),
            (0x03, b"1"),
        ]
    )
    trailing_auth_bytes: bytes = b""
    config_status: int = 200

    received_config: dict[int, bytes] | None = None
    client_public: bytes | None = None
    configured_called: bool = False
    _stream: object | None = None

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_post("/auth-setup", self._auth_setup)
        app.router.add_post("/config", self._config)
        app.router.add_post("/configured", self._configured)
        return app

    async def _auth_setup(self, request: web.Request) -> web.Response:
        body = await request.read()
        assert body[0] == 1 and len(body) == 33
        self.client_public = body[1:]
        private = X25519PrivateKey.generate()
        secret = private.exchange(X25519PublicKey.from_public_bytes(self.client_public))
        key = hashlib.sha1(b"AES-KEY" + secret).digest()[:16]
        iv = hashlib.sha1(b"AES-IV" + secret).digest()[:16]
        self._stream = Cipher(algorithms.AES(key), modes.CTR(iv)).encryptor()
        public = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        enc_sig = self._stream.update(self.signature)
        payload = (
            public
            + struct.pack(">I", len(self.certificate))
            + self.certificate
            + struct.pack(">I", len(enc_sig))
            + enc_sig
            + self.trailing_auth_bytes
        )
        return web.Response(body=payload, content_type="application/octet-stream")

    async def _config(self, request: web.Request) -> web.Response:
        if self.config_status != 200:
            return web.Response(status=self.config_status)
        assert self._stream is not None, "config before auth-setup"
        self.received_config = simple_tlv_decode(self._stream.update(await request.read()))
        if not self.response_fields:
            return web.Response(body=b"", content_type="application/octet-stream")
        out = self._stream.update(simple_tlv_encode(self.response_fields))
        return web.Response(body=out, content_type="application/octet-stream")

    async def _configured(self, request: web.Request) -> web.Response:
        assert await request.read() == b""
        self.configured_called = True
        return web.Response()
