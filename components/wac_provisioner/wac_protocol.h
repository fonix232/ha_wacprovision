#pragma once

// Pure WAC protocol code: no Wi-Fi, no ESPHome, only the standard library and mbedTLS.
// Built for the device and for host tests that run it against aiowac's fake accessory.
// See .agents/skills/wac-protocol/SKILL.md.

#include <cstddef>
#include <cstdint>
#include <string>
#include <utility>
#include <vector>

#include <mbedtls/aes.h>

namespace esphome::wac_provisioner::protocol {

using Bytes = std::vector<uint8_t>;
using TlvItems = std::vector<std::pair<uint8_t, Bytes>>;

/// TLV8. Values over 255 bytes are split into 255-byte fragments of the same type.
Bytes tlv_encode(const TlvItems &items);
/// Merges fragments (a 255-byte item followed by one of the same type). False if malformed.
bool tlv_decode(const uint8_t *data, size_t len, TlvItems &out);

struct Beacon {
  std::string wac_id;  // "00:11:22:33:44:55"
  std::string name, manufacturer, model;
  Bytes flags;
};
/// Payload after `00 A0 40 00` in the vendor IE. False without a 6-byte device ID.
bool parse_beacon_payload(const uint8_t *data, size_t len, Beacon &out);
/// Search a raw 802.11 IE list for the WAC vendor element. False if absent or malformed.
bool find_beacon_in_ies(const uint8_t *ies, size_t len, Beacon &out);

/// One continuous AES-128-CTR keystream: signature, then /config request, then response.
class KeyStream {
 public:
  KeyStream();
  ~KeyStream();
  KeyStream(const KeyStream &) = delete;
  KeyStream &operator=(const KeyStream &) = delete;

  /// key = SHA1("AES-KEY" || secret)[:16], iv = SHA1("AES-IV" || secret)[:16]
  bool init(const uint8_t shared_secret[32]);
  void apply(uint8_t *data, size_t len);

 private:
  mbedtls_aes_context aes_;
  unsigned char counter_[16]{};
  unsigned char block_[16]{};
  size_t offset_{0};
  bool ready_{false};
};

/// Random source: return 0 on success, like mbedTLS f_rng.
using Rng = int (*)(void *ctx, unsigned char *out, size_t len);

class Session {
 public:
  /// Generate this session's X25519 key pair.
  bool begin(Rng rng, void *rng_ctx);
  /// Body for POST /auth-setup: 0x01 || client public key.
  Bytes auth_setup_request() const;
  /// Parse the response, derive the stream, decrypt the signature.
  bool handle_auth_setup_response(const uint8_t *data, size_t len);
  /// Encrypted body for POST /config. Empty psk means an open network. Empty on bad input.
  Bytes config_request(const std::string &name, const std::string &ssid, const std::string &psk);
  /// Decrypt and parse the /config response. An empty response is valid.
  bool handle_config_response(const uint8_t *data, size_t len);

  const char *error() const { return this->error_; }
  size_t certificate_size() const { return this->certificate_size_; }

  std::string name, model, serial, firmware, hardware;

 private:
  bool fail_(const char *error) {
    this->error_ = error;
    return false;
  }

  Rng rng_{nullptr};
  void *rng_ctx_{nullptr};
  uint8_t private_key_[32]{};
  uint8_t public_key_[32]{};
  KeyStream stream_;
  bool authed_{false};
  bool configured_{false};
  size_t certificate_size_{0};
  const char *error_{""};
};

bool valid_psk(const std::string &psk);

}  // namespace esphome::wac_provisioner::protocol
