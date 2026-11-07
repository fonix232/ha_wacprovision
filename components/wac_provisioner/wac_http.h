#pragma once

// Minimal blocking HTTP/1.1 POST over BSD sockets (lwIP on the device, POSIX on the host).
// ESPHome excludes esp_http_client from IDF builds by default, and WAC needs only this.

#include <cstddef>
#include <cstdint>

#include "wac_protocol.h"

namespace esphome::wac_provisioner::http {

struct Response {
  int status{0};
  protocol::Bytes body;
};

/// POST `body` to http://<ipv4>:<port>/<path>. Every socket operation is bounded by
/// `timeout_ms`; bodies over `max_body` bytes fail. False on any transport error.
bool post(const char *ipv4, uint16_t port, const char *path, const protocol::Bytes &body, int timeout_ms,
          size_t max_body, Response &out);

}  // namespace esphome::wac_provisioner::http
