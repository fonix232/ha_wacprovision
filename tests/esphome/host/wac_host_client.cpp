// Host build of the ESPHome component's protocol code, for tests only.
//   wac_host_client selftest
//   wac_host_client provision <host> <port> <name> <ssid> <psk|"">
// Prints key=value lines; exits non-zero on failure.

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>

#include "wac_http.h"
#include "wac_protocol.h"

using namespace esphome::wac_provisioner::protocol;
namespace http = esphome::wac_provisioner::http;

static int urandom(void *, unsigned char *out, size_t len) {
  FILE *f = fopen("/dev/urandom", "rb");
  if (f == nullptr)
    return -1;
  size_t got = fread(out, 1, len, f);
  fclose(f);
  return got == len ? 0 : -1;
}

static int selftest() {
  // Example beacon IE from the BertoldVdb/WACResearch README.
  const char *hex = "dd2400a0400007060011223344550002700301044e616d6502067761636c6962030454797065";
  Bytes ie;
  for (size_t i = 0; hex[i] && hex[i + 1]; i += 2)
    ie.push_back((uint8_t) strtol(std::string(hex + i, 2).c_str(), nullptr, 16));
  Beacon b;
  if (!find_beacon_in_ies(ie.data(), ie.size(), b))
    return 1;
  printf("wac_id=%s\nname=%s\nmanufacturer=%s\nmodel=%s\n", b.wac_id.c_str(), b.name.c_str(), b.manufacturer.c_str(),
         b.model.c_str());

  Bytes big(512);
  for (size_t i = 0; i < big.size(); i++)
    big[i] = (uint8_t) i;
  Bytes enc = tlv_encode({{0x0B, big}, {0x0C, Bytes{'x'}}});
  TlvItems dec;
  bool frag_ok = enc.size() == 512 + 3 * 2 + 3 && enc[1] == 255 && enc[258] == 255 && enc[515] == 2 &&
                 tlv_decode(enc.data(), enc.size(), dec) && dec.size() == 2 && dec[0].second == big;
  const uint8_t repeats[] = {0x01, 0x01, 0xAA, 0x01, 0x01, 0xBB};
  bool no_merge = tlv_decode(repeats, sizeof(repeats), dec) && dec.size() == 2;
  const uint8_t truncated[] = {0x01, 0x05, 0xAA};
  bool rejects = !tlv_decode(truncated, sizeof(truncated), dec);
  printf("tlv_fragmentation=%s\ntlv_no_merge=%s\ntlv_truncated_rejected=%s\n", frag_ok ? "ok" : "FAIL",
         no_merge ? "ok" : "FAIL", rejects ? "ok" : "FAIL");
  bool psk_ok = valid_psk("testpassword") && !valid_psk("short") && valid_psk(std::string(64, 'a')) &&
                !valid_psk(std::string(64, 'x'));
  printf("psk_rules=%s\n", psk_ok ? "ok" : "FAIL");
  return frag_ok && no_merge && rejects && psk_ok ? 0 : 1;
}

static int provision(int argc, char **argv) {
  if (argc != 7)
    return 2;
  const char *host = argv[2];
  uint16_t port = (uint16_t) atoi(argv[3]);
  Session s;
  if (!s.begin(urandom, nullptr)) {
    printf("error=%s\n", s.error());
    return 1;
  }
  http::Response resp;
  if (!http::post(host, port, "auth-setup", s.auth_setup_request(), 5000, 8192, resp) || resp.status != 200) {
    printf("error=auth-setup HTTP %d\n", resp.status);
    return 1;
  }
  if (!s.handle_auth_setup_response(resp.body.data(), resp.body.size())) {
    printf("error=%s\n", s.error());
    return 1;
  }
  Bytes body = s.config_request(argv[4], argv[5], argv[6]);
  if (body.empty()) {
    printf("error=%s\n", s.error());
    return 1;
  }
  if (!http::post(host, port, "config", body, 5000, 8192, resp) || resp.status != 200) {
    printf("error=config HTTP %d\n", resp.status);
    return 1;
  }
  if (!s.handle_config_response(resp.body.data(), resp.body.size())) {
    printf("error=%s\n", s.error());
    return 1;
  }
  printf("certificate_size=%zu\nname=%s\nmodel=%s\nserial=%s\nfirmware=%s\nhardware=%s\n", s.certificate_size(),
         s.name.c_str(), s.model.c_str(), s.serial.c_str(), s.firmware.c_str(), s.hardware.c_str());
  return 0;
}

int main(int argc, char **argv) {
  if (argc >= 2 && strcmp(argv[1], "selftest") == 0)
    return selftest();
  if (argc >= 2 && strcmp(argv[1], "provision") == 0)
    return provision(argc, argv);
  fprintf(stderr, "usage: %s selftest | provision <host> <port> <name> <ssid> <psk>\n", argv[0]);
  return 2;
}
