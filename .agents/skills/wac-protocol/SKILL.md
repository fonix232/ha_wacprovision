---
name: wac-protocol
description: "Apple WAC (Wireless Accessory Configuration) wire format, crypto and traps. Use when working on aiowac, the ESPHome protocol code, beacon parsing, auth-setup, /config or /configured."
---

# WAC protocol (configurator side)

Reference implementation: BertoldVdb/WACResearch (`waclib/`, MIT). The official spec is MFi
NDA material; nothing here comes from it.

## Discovery

- Unconfigured accessories run an **open** soft-AP. Their beacons carry a vendor IE:
  `DD <len> 00 A0 40 00 <TLV8...>`. Fields: `0x07` device ID (6-byte MAC), `0x00` flags,
  `0x01` name, `0x02` manufacturer, `0x03` model. Unknown types must be kept, not rejected.
- On the soft-AP the accessory advertises `_mfi-config._tcp` with TXT `deviceid`, `seed`,
  `features`, `flags`, `protovers`, `srcvers`. The port is not fixed: discover it.

## Exchange (plain HTTP POST, `Content-Type: application/octet-stream`, `Connection: close`)

1. `POST /auth-setup`, body `01 || client_x25519_pub(32)`.
   Response: `accessory_pub(32) || u32be len || cert (PKCS#7, <=1280) || u32be len || sig (<=256)`.
   Nothing may follow the signature.
2. `secret = X25519(client_priv, accessory_pub)`.
   `key = SHA1("AES-KEY" || secret)[:16]`, `iv = SHA1("AES-IV" || secret)[:16]`, AES-128-CTR.
3. Decrypt `sig` with the stream. Verification (RSA/SHA1 for 2.0C chips, ECDSA/SHA256 for
   3.0) signs `hash(accessory_pub || client_pub)`; not implemented yet.
4. `POST /config`, body = stream-encrypted TLV8: `0x08` name, `0x0C` SSID, `0x0B` PSK (only
   if 8+ chars), `0x09` AirPlay key (optional). Response = stream-decrypted TLV8, may be
   empty: `0x08` name, `0x07` model, `0x0A` serial, `0x02` firmware, `0x03` hardware,
   `0x01` bundle seed.
5. Accessory leaves the soft-AP and joins the target network. The configurator rejoins that
   network, finds `_mfi-config._tcp` there (match `deviceid`) and sends `POST /configured`
   with an empty, unencrypted body. 200 = done.

## Traps

- **One CTR stream for steps 3 and 4**, in that order: signature, request, response. A new
  cipher per message produces garbage that looks like a TLV parse error.
- TLV8 values over 255 bytes are split into consecutive same-type fragments; merge
  adjacent same-type items on decode. The reference client panics instead; we must not.
- The accessory does not authenticate the configurator. Never provision automatically.
- `deviceid` on `_mfi-config` may or may not equal the HAP `id`; do not assume either way.
