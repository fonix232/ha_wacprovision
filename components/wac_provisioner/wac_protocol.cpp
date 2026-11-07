#include "wac_protocol.h"

#include <cstdio>
#include <cstring>

#include <mbedtls/ecdh.h>
#include <mbedtls/ecp.h>
#include <mbedtls/sha1.h>
#include <mbedtls/version.h>

namespace esphome::wac_provisioner::protocol {

namespace {

constexpr uint8_t WAC_PREFIX[4] = {0x00, 0xA0, 0x40, 0x00};
constexpr size_t MAX_CERTIFICATE = 1280;
constexpr size_t MAX_SIGNATURE = 256;

// /config request and response fields.
constexpr uint8_t REQ_NAME = 0x08, REQ_SSID = 0x0C, REQ_PSK = 0x0B;
constexpr uint8_t RESP_FIRMWARE = 0x02, RESP_HARDWARE = 0x03, RESP_MODEL = 0x07, RESP_NAME = 0x08,
                  RESP_SERIAL = 0x0A;

int sha1(const unsigned char *in, size_t len, unsigned char out[20]) {
#if MBEDTLS_VERSION_MAJOR >= 3
  return mbedtls_sha1(in, len, out);
#else
  return mbedtls_sha1_ret(in, len, out);
#endif
}

uint32_t read_u32be(const uint8_t *p) {
  return (uint32_t(p[0]) << 24) | (uint32_t(p[1]) << 16) | (uint32_t(p[2]) << 8) | uint32_t(p[3]);
}

std::string to_string(const Bytes &b) { return std::string(b.begin(), b.end()); }

const Bytes *find(const TlvItems &items, uint8_t type) {
  const Bytes *found = nullptr;
  for (const auto &item : items) {
    if (item.first == type)
      found = &item.second;  // last one wins, like aiowac
  }
  return found;
}

}  // namespace

Bytes tlv_encode(const TlvItems &items) {
  Bytes out;
  for (const auto &item : items) {
    const Bytes &value = item.second;
    if (value.empty()) {
      out.push_back(item.first);
      out.push_back(0);
      continue;
    }
    for (size_t pos = 0; pos < value.size(); pos += 255) {
      size_t n = std::min<size_t>(255, value.size() - pos);
      out.push_back(item.first);
      out.push_back(static_cast<uint8_t>(n));
      out.insert(out.end(), value.begin() + pos, value.begin() + pos + n);
    }
  }
  return out;
}

bool tlv_decode(const uint8_t *data, size_t len, TlvItems &out) {
  out.clear();
  bool continues = false;
  size_t pos = 0;
  while (pos < len) {
    if (len - pos < 2)
      return false;
    uint8_t type = data[pos], n = data[pos + 1];
    pos += 2;
    if (n > len - pos)
      return false;
    if (continues && !out.empty() && out.back().first == type) {
      out.back().second.insert(out.back().second.end(), data + pos, data + pos + n);
    } else {
      out.emplace_back(type, Bytes(data + pos, data + pos + n));
    }
    pos += n;
    continues = n == 255;
  }
  return true;
}

bool parse_beacon_payload(const uint8_t *data, size_t len, Beacon &out) {
  TlvItems items;
  if (!tlv_decode(data, len, items))
    return false;
  const Bytes *id = find(items, 0x07);
  if (id == nullptr || id->size() != 6)
    return false;
  char buf[18];
  snprintf(buf, sizeof(buf), "%02X:%02X:%02X:%02X:%02X:%02X", (*id)[0], (*id)[1], (*id)[2], (*id)[3], (*id)[4],
           (*id)[5]);
  out.wac_id = buf;
  if (const Bytes *v = find(items, 0x00))
    out.flags = *v;
  if (const Bytes *v = find(items, 0x01))
    out.name = to_string(*v);
  if (const Bytes *v = find(items, 0x02))
    out.manufacturer = to_string(*v);
  if (const Bytes *v = find(items, 0x03))
    out.model = to_string(*v);
  return true;
}

bool find_beacon_in_ies(const uint8_t *ies, size_t len, Beacon &out) {
  size_t pos = 0;
  while (pos + 2 <= len) {
    uint8_t id = ies[pos], n = ies[pos + 1];
    if (n > len - pos - 2)
      return false;
    const uint8_t *body = ies + pos + 2;
    if (id == 0xDD && n >= 4 && memcmp(body, WAC_PREFIX, 4) == 0)
      return parse_beacon_payload(body + 4, n - 4, out);
    pos += 2 + n;
  }
  return false;
}

KeyStream::KeyStream() { mbedtls_aes_init(&this->aes_); }
KeyStream::~KeyStream() { mbedtls_aes_free(&this->aes_); }

bool KeyStream::init(const uint8_t shared_secret[32]) {
  unsigned char buf[7 + 32], digest[20];
  memcpy(buf, "AES-KEY", 7);
  memcpy(buf + 7, shared_secret, 32);
  if (sha1(buf, 7 + 32, digest) != 0 || mbedtls_aes_setkey_enc(&this->aes_, digest, 128) != 0)
    return false;
  memcpy(buf, "AES-IV", 6);
  memcpy(buf + 6, shared_secret, 32);
  if (sha1(buf, 6 + 32, digest) != 0)
    return false;
  memcpy(this->counter_, digest, 16);
  this->offset_ = 0;
  this->ready_ = true;
  return true;
}

void KeyStream::apply(uint8_t *data, size_t len) {
  if (this->ready_ && len > 0)
    mbedtls_aes_crypt_ctr(&this->aes_, len, &this->offset_, this->counter_, this->block_, data, data);
}

bool Session::begin(Rng rng, void *rng_ctx) {
  this->rng_ = rng;
  this->rng_ctx_ = rng_ctx;
  mbedtls_ecp_group grp;
  mbedtls_mpi d;
  mbedtls_ecp_point q;
  mbedtls_ecp_group_init(&grp);
  mbedtls_mpi_init(&d);
  mbedtls_ecp_point_init(&q);
  size_t olen = 0;
  bool ok = mbedtls_ecp_group_load(&grp, MBEDTLS_ECP_DP_CURVE25519) == 0 &&
            mbedtls_ecp_gen_keypair(&grp, &d, &q, rng, rng_ctx) == 0 &&
            mbedtls_mpi_write_binary_le(&d, this->private_key_, 32) == 0 &&
            mbedtls_ecp_point_write_binary(&grp, &q, MBEDTLS_ECP_PF_UNCOMPRESSED, &olen, this->public_key_, 32) == 0 &&
            olen == 32;
  mbedtls_ecp_point_free(&q);
  mbedtls_mpi_free(&d);
  mbedtls_ecp_group_free(&grp);
  return ok ? true : this->fail_("key generation failed");
}

Bytes Session::auth_setup_request() const {
  Bytes out;
  out.reserve(33);
  out.push_back(0x01);
  out.insert(out.end(), this->public_key_, this->public_key_ + 32);
  return out;
}

bool Session::handle_auth_setup_response(const uint8_t *data, size_t len) {
  if (this->authed_)
    return this->fail_("auth-setup already done");
  if (len < 32 + 4)
    return this->fail_("auth-setup response too short");
  const uint8_t *peer = data;
  size_t pos = 32;
  uint32_t cert_len = read_u32be(data + pos);
  pos += 4;
  if (cert_len > MAX_CERTIFICATE || cert_len > len - pos)
    return this->fail_("auth-setup certificate length invalid");
  this->certificate_size_ = cert_len;
  pos += cert_len;
  if (len - pos < 4)
    return this->fail_("auth-setup signature length missing");
  uint32_t sig_len = read_u32be(data + pos);
  pos += 4;
  if (sig_len > MAX_SIGNATURE || sig_len > len - pos)
    return this->fail_("auth-setup signature length invalid");
  if (pos + sig_len != len)
    return this->fail_("trailing data after auth-setup signature");

  uint8_t secret[32];
  mbedtls_ecp_group grp;
  mbedtls_mpi d, z;
  mbedtls_ecp_point qp;
  mbedtls_ecp_group_init(&grp);
  mbedtls_mpi_init(&d);
  mbedtls_mpi_init(&z);
  mbedtls_ecp_point_init(&qp);
  bool ok = mbedtls_ecp_group_load(&grp, MBEDTLS_ECP_DP_CURVE25519) == 0 &&
            mbedtls_mpi_read_binary_le(&d, this->private_key_, 32) == 0 &&
            mbedtls_ecp_point_read_binary(&grp, &qp, peer, 32) == 0 &&
            mbedtls_ecdh_compute_shared(&grp, &z, &qp, &d, this->rng_, this->rng_ctx_) == 0 &&
            mbedtls_mpi_write_binary_le(&z, secret, 32) == 0;
  mbedtls_ecp_point_free(&qp);
  mbedtls_mpi_free(&z);
  mbedtls_mpi_free(&d);
  mbedtls_ecp_group_free(&grp);
  if (!ok)
    return this->fail_("accessory sent an invalid X25519 public key");
  uint8_t acc = 0;
  for (uint8_t b : secret)
    acc |= b;
  if (acc == 0)
    return this->fail_("accessory sent a low-order X25519 public key");
  if (!this->stream_.init(secret))
    return this->fail_("key derivation failed");
  memset(secret, 0, sizeof(secret));

  // Decrypt the signature to advance the stream. Verification is not implemented yet.
  Bytes signature(data + pos, data + pos + sig_len);
  this->stream_.apply(signature.data(), signature.size());
  this->authed_ = true;
  return true;
}

bool valid_psk(const std::string &psk) {
  if (psk.size() == 64) {
    for (char c : psk) {
      if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F')))
        return false;
    }
    return true;
  }
  return psk.size() >= 8 && psk.size() <= 63;
}

Bytes Session::config_request(const std::string &name, const std::string &ssid, const std::string &psk) {
  if (!this->authed_) {
    this->fail_("auth-setup must come before config");
    return {};
  }
  if (this->configured_) {
    this->fail_("config already sent in this session");
    return {};
  }
  if (name.empty() || ssid.empty() || ssid.size() > 32 || (!psk.empty() && !valid_psk(psk))) {
    this->fail_("invalid name, SSID or PSK");
    return {};
  }
  TlvItems items{{REQ_NAME, Bytes(name.begin(), name.end())}, {REQ_SSID, Bytes(ssid.begin(), ssid.end())}};
  if (!psk.empty())
    items.emplace_back(REQ_PSK, Bytes(psk.begin(), psk.end()));
  Bytes body = tlv_encode(items);
  for (auto &item : items)
    std::fill(item.second.begin(), item.second.end(), 0);
  this->stream_.apply(body.data(), body.size());
  this->configured_ = true;
  return body;
}

bool Session::handle_config_response(const uint8_t *data, size_t len) {
  if (len == 0)
    return true;
  Bytes plain(data, data + len);
  this->stream_.apply(plain.data(), plain.size());
  TlvItems items;
  if (!tlv_decode(plain.data(), plain.size(), items))
    return this->fail_("malformed /config response");
  if (const Bytes *v = find(items, RESP_NAME))
    this->name = to_string(*v);
  if (const Bytes *v = find(items, RESP_MODEL))
    this->model = to_string(*v);
  if (const Bytes *v = find(items, RESP_SERIAL))
    this->serial = to_string(*v);
  if (const Bytes *v = find(items, RESP_FIRMWARE))
    this->firmware = to_string(*v);
  if (const Bytes *v = find(items, RESP_HARDWARE))
    this->hardware = to_string(*v);
  return true;
}

}  // namespace esphome::wac_provisioner::protocol
