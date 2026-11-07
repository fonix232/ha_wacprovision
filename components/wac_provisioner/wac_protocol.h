#pragma once

// Pure WAC protocol code: no Wi-Fi, no ESPHome. Built for the device and for host tests
// that replay aiowac's vectors. See .agents/skills/wac-protocol/SKILL.md.

#include <cstdint>
#include <optional>
#include <string>
#include <utility>
#include <vector>

namespace esphome::wac_provisioner::protocol {

using Bytes = std::vector<uint8_t>;

// TLV8 with >255-byte fragmentation (encode) and merging (decode).
Bytes tlv_encode(const std::vector<std::pair<uint8_t, Bytes>> &items);
std::optional<std::vector<std::pair<uint8_t, Bytes>>> tlv_decode(const Bytes &data);

struct Beacon {
  std::string wac_id, name, manufacturer, model;
  Bytes flags;
};
// Payload after `DD <len> 00 A0 40 00`.
std::optional<Beacon> parse_beacon_payload(const uint8_t *data, size_t len);

// One continuous AES-128-CTR stream: signature, then /config request, then response.
class KeyStream {
 public:
  bool init(const uint8_t shared_secret[32]);  // SHA1("AES-KEY"|s)[:16], SHA1("AES-IV"|s)[:16]
  void apply(uint8_t *data, size_t len);
 private:
  uint8_t key_[16]{}, counter_[16]{}, block_[16]{};
  size_t offset_{16};
};

class Session {
 public:
  // Body for POST /auth-setup: 0x01 || X25519 public key.
  Bytes auth_setup_request();
  // Parses the response, derives the stream, decrypts the signature. False on bad input.
  bool handle_auth_setup_response(const Bytes &response);
  // Encrypted body for POST /config.
  Bytes config_request(const std::string &name, const std::string &ssid, const std::string &psk);
  // Decrypts and parses the /config response; empty response is valid (nullopt fields).
  bool handle_config_response(const Bytes &response);

  std::string serial, model, firmware, hardware;

 private:
  uint8_t private_key_[32]{}, public_key_[32]{};
  KeyStream stream_;
};

}  // namespace esphome::wac_provisioner::protocol
