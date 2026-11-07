#pragma once

// Sketch for v1. Protocol pieces (wac_protocol.h) are pure and host-testable; this class
// owns the radio, the state machine and the recovery guards. Bodies are stubs.

#include <cstdint>
#include <functional>
#include <string>

#include "esphome/core/automation.h"
#include "esphome/core/component.h"
#include "esphome/core/helpers.h"

namespace esphome::wac_provisioner {

struct WacAccessory {
  std::string wac_id;  // "00:11:22:33:44:55"
  std::string bssid;
  uint8_t channel{0};
  int8_t rssi{0};
  std::string name, manufacturer, model;
};

enum class WacStage : uint8_t { JOIN, DISCOVER, AUTH_SETUP, CONFIG_SENT, DONE };

// Must match ProvisionStage in custom_components/wac_provision/models.py.
inline const char *stage_str(WacStage s) {
  switch (s) {
    case WacStage::JOIN: return "join";
    case WacStage::DISCOVER: return "discover";
    case WacStage::AUTH_SETUP: return "auth_setup";
    case WacStage::CONFIG_SENT: return "config_sent";
    case WacStage::DONE: return "done";
  }
  return "config_sent";  // unknown: assume the worst, never retry elsewhere
}

struct WacResult {
  std::string session;
  bool ok{false};
  WacStage stage{WacStage::JOIN};
  std::string error;  // empty when ok
  std::string serial, model, firmware, hardware;
  std::string log_tail;
  bool aborted_by_reboot{false};
};

enum class WacState : uint8_t {
  IDLE,
  DISCONNECTING,   // take the radio from the wifi component
  JOINING_SOFTAP,
  DISCOVERING,     // _mfi-config._tcp via IDF mdns_query_ptr
  AUTH_SETUP,
  CONFIG,
  REJOINING,       // hand the radio back; wifi component reconnects
  REPORTING,       // wait for API connection, then fire on_result
};

class WacProvisioner : public Component {
 public:
  void setup() override;  // checks the RTC_NOINIT marker from a previous, interrupted run
  void loop() override;   // drives the state machine; passive scan on scan_interval
  float get_setup_priority() const override;

  void set_scan_interval(uint32_t ms) { this->scan_interval_ms_ = ms; }
  void set_deadline(uint32_t ms) { this->deadline_ms_ = ms; }
  void set_log_cache_size(size_t bytes) { this->log_cache_size_ = bytes; }

  /// Called from the `wac_provision` API action. Empty ssid/psk: use this node's own Wi-Fi.
  /// Returns false (and reports a result) when a session is already running.
  bool provision(const std::string &session, const std::string &bssid, const std::string &name,
                 const std::string &ssid, const std::string &psk);

  void add_on_found_callback(std::function<void(const WacAccessory &)> &&cb) { this->found_cbs_.add(std::move(cb)); }
  void add_on_result_callback(std::function<void(const WacResult &)> &&cb) { this->result_cbs_.add(std::move(cb)); }

 protected:
  void start_scan_();
  void on_scan_done_();       // parse vendor IEs (OUI 00:A0:40), fire on_found
  void enter_(WacState state);
  void fail_(WacStage stage, const char *error);
  void restore_wifi_();       // idempotent; also called from the deadline timer
  static void deadline_cb_(void *arg);  // esp_timer callback, runs outside loop()

  uint32_t scan_interval_ms_{300000};
  uint32_t deadline_ms_{45000};
  size_t log_cache_size_{4096};

  WacState state_{WacState::IDLE};
  WacResult pending_;
  CallbackManager<void(const WacAccessory &)> found_cbs_;
  CallbackManager<void(const WacResult &)> result_cbs_;
};

class FoundTrigger : public Trigger<const WacAccessory &> {
 public:
  explicit FoundTrigger(WacProvisioner *parent) {
    parent->add_on_found_callback([this](const WacAccessory &a) { this->trigger(a); });
  }
};

class ResultTrigger : public Trigger<const WacResult &> {
 public:
  explicit ResultTrigger(WacProvisioner *parent) {
    parent->add_on_result_callback([this](const WacResult &r) { this->trigger(r); });
  }
};

}  // namespace esphome::wac_provisioner
