from __future__ import annotations

import hashlib

import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from aiowac import WacProtocolError, find_in_ies, parse_payload, tlv
from aiowac.crypto import KeyStream, derive_key_iv

# Example beacon IE from the BertoldVdb/WACResearch README (placeholder MAC 00:11:22:33:44:55).
README_IE = bytes.fromhex("dd2400a0400007060011223344550002700301044e616d6502067761636c6962030454797065")


def test_tlv_roundtrip():
    items = [(0x08, b"name"), (0x0C, b"TestSSID"), (0x01, b"")]
    assert tlv.decode(tlv.encode(items)) == items


def test_tlv_fragments_long_values():
    value = bytes(range(256)) * 2  # 512 bytes -> 255 + 255 + 2
    encoded = tlv.encode([(0x0B, value), (0x0C, b"x")])
    assert encoded[0:2] == b"\x0b\xff" and encoded[257:259] == b"\x0b\xff" and encoded[514:516] == b"\x0b\x02"
    assert tlv.decode(encoded) == [(0x0B, value), (0x0C, b"x")]


def test_tlv_does_not_merge_short_repeats():
    encoded = bytes.fromhex("0101aa0101bb")
    assert tlv.decode(encoded) == [(0x01, b"\xaa"), (0x01, b"\xbb")]


@pytest.mark.parametrize("bad", ["01", "0105aabb"])
def test_tlv_truncated(bad):
    with pytest.raises(WacProtocolError):
        tlv.decode(bytes.fromhex(bad))


def test_readme_beacon_vector():
    beacon = find_in_ies(README_IE)
    assert beacon is not None
    assert beacon.device_id == "00:11:22:33:44:55"
    assert (beacon.name, beacon.manufacturer, beacon.model) == ("Name", "waclib", "Type")
    assert beacon.flags == bytes.fromhex("7003")


def test_beacon_found_among_other_ies():
    ssid_ie = b"\x00\x04Plug"
    other_vendor = bytes.fromhex("dd0500500f0201")
    assert find_in_ies(ssid_ie + other_vendor + README_IE) == find_in_ies(README_IE)
    assert find_in_ies(ssid_ie + other_vendor) is None


def test_beacon_keeps_unknown_fields():
    beacon = parse_payload(bytes.fromhex("0706001122334455") + b"\x09\x02hi")
    assert beacon.extra == {0x09: b"hi"}


def test_beacon_without_device_id_rejected():
    with pytest.raises(WacProtocolError):
        parse_payload(b"\x01\x04Name")


def test_kdf_matches_airplay_auth_setup_definition():
    secret = bytes(range(32))
    key, iv = derive_key_iv(secret)
    assert key == hashlib.sha1(b"AES-KEY" + secret).digest()[:16]
    assert iv == hashlib.sha1(b"AES-IV" + secret).digest()[:16]


def test_keystream_is_continuous():
    key, iv = derive_key_iv(b"\x01" * 32)
    data = bytes(range(100))
    stream = KeyStream(key, iv)
    split = stream.apply(data[:37]) + stream.apply(data[37:])
    whole = Cipher(algorithms.AES(key), modes.CTR(iv)).encryptor().update(data)
    assert split == whole
