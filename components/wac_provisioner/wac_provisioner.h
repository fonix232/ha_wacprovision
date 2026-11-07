#pragma once

// WAC radio for the wac_provision HA integration.
//
// Detection is passive: while connected, a periodic scan runs with the driver's vendor-IE
// callback registered; beacons carrying Apple's WAC element (OUI 00:A0:40, type 0) are
// matched to ESPHome's own scan results for SSID and channel, then reported via on_found.
//
// Provisioning runs in its own FreeRTOS task, because mDNS and HTTP block: the main loop
// hands the radio over by disabling the wifi component, the task joins the accessory's
// soft-AP, runs auth-setup and /config, and stops the radio; the main loop then re-enables
// wifi and reports the result once the API is connected again.
//
// Recovery guards (all independent of the main loop):
//   - per-step timeouts inside the task;
//   - a deadline esp_timer that requests an abort, after which the main loop takes the
//     radio back even if the task is stuck;
//   - a backstop esp_timer that calls esp_restart() if the radio is still not back with
//     the wifi component well after the deadline (wifi.disable() pauses ESPHome's own
//     reboot_timeout, so this replaces it while we hold the radio);
//   - an RTC_NOINIT marker: after any reboot mid-session, setup() reports the session as
//     aborted_by_reboot with the stage it reached and the saved log tail.

#include <array>
#include <atomic>
#include <cstdint>
#include <functional>
#include <mutex>
#include <string>
#include <vector>

#include "esphome/components/wifi/wifi_component.h"
#include "esphome/core/automation.h"
#include "esphome/core/component.h"
#include "esphome/core/helpers.h"

#include <esp_timer.h>
#include <esp_wifi_types.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

#include "wac_protocol.h"

namespace esphome::wac_provisioner {

struct WacAccessory {
  std::string wac_id;  // "00:11:22:33:44:55"
  std::string bssid;   // "00:11:22:33:44:56"
  std::string ssid;    // the accessory's soft-AP SSID
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

class WacProvisioner : public Component
#ifdef USE_WIFI_SCAN_RESULTS_LISTENERS
    , public wifi::WiFiScanResultsListener
#endif
{
 public:
  void setup() override;
  void loop() override;
  void dump_config() override;
  float get_setup_priority() const override { return setup_priority::AFTER_WIFI; }

  void set_scan_interval(uint32_t ms) { this->scan_interval_ms_ = ms; }
  void set_deadline(uint32_t ms) { this->deadline_ms_ = ms; }
  void set_log_cache_size(size_t bytes) { this->log_cache_size_ = bytes; }

  /// Called from the `wac_provision` API action; returns immediately. Empty ssid/psk:
  /// use this node's own Wi-Fi. A refusal (busy, unknown BSSID) is reported via on_result.
  void provision(const std::string &session, const std::string &bssid, const std::string &name,
                 const std::string &ssid, const std::string &psk);

  void add_on_found_callback(std::function<void(const WacAccessory &)> &&cb) { this->found_cbs_.add(std::move(cb)); }
  void add_on_result_callback(std::function<void(const WacResult &)> &&cb) { this->result_cbs_.add(std::move(cb)); }

#ifdef USE_WIFI_SCAN_RESULTS_LISTENERS
  void on_wifi_scan_results(const wifi::wifi_scan_vector_t<wifi::WiFiScanResult> &results) override;
#endif

 protected:
  enum class State : uint8_t {
    IDLE,
    HANDING_OVER,  // wifi.disable() called; start the task next loop
    RUNNING,       // session task owns the radio
    RECLAIMING,    // task finished or was abandoned; give the radio back
    REPORTING,     // wifi re-enabled; wait for the API, then fire on_result
  };

  struct Sighting {
    uint8_t bssid[6];
    std::string ssid;
    uint8_t channel;
    int8_t rssi;
    protocol::Beacon beacon;
    uint32_t seen_ms;
  };

  // Shared with the session task: inputs are written before the task starts, outputs are
  // read after task_done_ is set.
  struct Job {
    std::string session, name, target_ssid, target_psk, softap_ssid;
    uint8_t bssid[6]{};
    uint8_t channel{0};
    std::string wac_id;
    std::atomic<uint8_t> stage{static_cast<uint8_t>(WacStage::JOIN)};
    std::atomic<bool> abort{false};
    bool ok{false};
    std::string error, serial, model, firmware, hardware;
  };

  void start_scan_();
  static void vendor_ie_cb_(void *ctx, wifi_vendor_ie_type_t type, const uint8_t sa[6],
                            const vendor_ie_data_t *ie, int rssi);
  static void session_task_(void *arg);
  static void run_session_(Job &job);
  static void deadline_cb_(void *arg);
  static void backstop_cb_(void *arg);
  static void log_cb_(void *self, uint8_t level, const char *tag, const char *message, size_t len);

  void refuse_(const std::string &session, WacStage stage, const char *error);
  void finish_job_();
  void report_(WacResult result);
  void stop_timers_();
  std::string log_tail_();

  uint32_t scan_interval_ms_{300000};
  uint32_t deadline_ms_{45000};
  size_t log_cache_size_{4096};

  State state_{State::IDLE};
  uint32_t state_since_{0};
  uint32_t last_scan_{0};
  bool vendor_cb_registered_{false};
  bool scan_pending_{false};

  std::mutex ie_lock_;
  std::vector<std::pair<std::array<uint8_t, 6>, std::pair<int8_t, protocol::Beacon>>> ie_hits_;
  std::vector<Sighting> sightings_;

  Job *job_{nullptr};
  TaskHandle_t task_{nullptr};
  std::atomic<bool> task_done_{false};
  std::atomic<bool> deadline_hit_{false};
  esp_timer_handle_t deadline_timer_{nullptr};
  esp_timer_handle_t backstop_timer_{nullptr};
  WacResult pending_;

  std::mutex log_lock_;
  std::atomic<bool> capturing_{false};
  std::string log_cache_;

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
