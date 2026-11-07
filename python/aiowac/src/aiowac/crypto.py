"""Key agreement and the session keystream.

One AES-128-CTR stream per session, used in this order: decrypt the auth-setup signature,
encrypt the /config request, decrypt the /config response.
"""

from __future__ import annotations

import hashlib

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from .exceptions import WacProtocolError


class KeyStream:
    """A continuous AES-128-CTR keystream. XOR is the same operation both ways."""

    def __init__(self, key: bytes, iv: bytes) -> None:
        self._ctx = Cipher(algorithms.AES(key), modes.CTR(iv)).encryptor()

    def apply(self, data: bytes) -> bytes:
        return self._ctx.update(data)


def derive_key_iv(shared_secret: bytes) -> tuple[bytes, bytes]:
    key = hashlib.sha1(b"AES-KEY" + shared_secret).digest()[:16]
    iv = hashlib.sha1(b"AES-IV" + shared_secret).digest()[:16]
    return key, iv


def public_bytes(private_key: X25519PrivateKey) -> bytes:
    return private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


def agree(private_key: X25519PrivateKey, peer_public: bytes) -> KeyStream:
    if len(peer_public) != 32:
        raise WacProtocolError("accessory public key is not 32 bytes")
    try:
        secret = private_key.exchange(X25519PublicKey.from_public_bytes(peer_public))
    except ValueError as err:  # low-order point: all-zero shared secret
        raise WacProtocolError("accessory sent an invalid X25519 public key") from err
    return KeyStream(*derive_key_iv(secret))
