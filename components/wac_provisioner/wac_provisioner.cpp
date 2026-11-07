#include "wac_provisioner.h"

#include <cinttypes>
#include <cstring>

#include "esphome/core/application.h"
#include "esphome/core/hal.h"
#include "esphome/core/log.h"

#ifdef USE_API
#include "esphome/components/api/api_server.h"
#endif
#ifdef USE_LOGGER
#include "esphome/components/logger/logger.h"
#endif

#include <esp_attr.h>
#include <esp_event.h>
#include <esp_netif.h>
#include <esp_random.h>
#include <esp_system.h>
#include <esp_wifi.h>
#include <freertos/event_groups.h>
#include <lwip/inet.h>

#ifdef USE_MDNS
#include <mdns.h>
#endif

#include "wac_http.h"

namespace esphome::wac_provisioner {

static const char *const TAG = "wac_provisioner";

static constexpr uint32_t JOIN_TIMEOUT_MS = 15000;
static constexpr uint32_t MDNS_TIMEOUT_MS = 4000;
static constexpr int HTTP_TIMEOUT_MS = 8000;
static constexpr size_t HTTP_MAX_BODY = 8192;
static constexpr uint32_t ABANDON_GRACE_MS = 10000;   // after the deadline, before killing the task
static constexpr uint32_t BACKSTOP_EXTRA_MS = 60000;  // after the deadline, before esp_restart()
static constexpr uint32_t REPORT_WAIT_MS = 120000;    // fire on_result even if no API client by then
static constexpr uint32_t SIGHTING_TTL_MS = 30 * 60 * 1000;
static constexpr size_t LOG_TAIL_REPORT = 1000;

// ---- RTC record: survives esp_restart(), not power loss ----------------------------------

static constexpr uint32_t RTC_MAGIC = 0x57414331;  // "WAC1"
struct RtcRecord {
  uint32_t magic;
  uint8_t stage;
  char session[16];
  uint16_t log_len;
  char log[480];
};
RTC_NOINIT_ATTR static RtcRecord s_rtc;

static void rtc_begin(const std::string &session) {
  memset(&s_rtc, 0, sizeof(s_rtc));
  strncpy(s_rtc.session, session.c_str(), sizeof(s_rtc.session) - 1);
  s_rtc.stage = static_cast<uint8_t>(WacStage::JOIN);
  s_rtc.magic = RTC_MAGIC;
}
static void rtc_clear() { s_rtc.magic = 0; }

static int esp_rng(void *, unsigned char *out, size_t len) {
  esp_fill_random(out, len);
  return 0;
}

static std::string mac_str(const uint8_t *m) {
  char buf[18];
  snprintf(buf, sizeof(buf), "%02X:%02X:%02X:%02X:%02X:%02X", m[0], m[1], m[2], m[3], m[4], m[5]);
  return buf;
}

static bool parse_mac(const std::string &s, uint8_t out[6]) {
  unsigned v[6];
  if (sscanf(s.c_str(), "%x:%x:%x:%x:%x:%x", &v[0], &v[1], &v[2], &v[3], &v[4], &v[5]) != 6)
    return false;
  for (int i = 0; i < 6; i++)
    out[i] = static_cast<uint8_t>(v[i]);
  return true;
}

// ---- setup / config ----------------------------------------------------------------------

void WacProvisioner::setup() {
#ifdef USE_LOGGER
  if (logger::global_logger != nullptr)
    logger::global_logger->add_log_callback(this, WacProvisioner::log_cb_);
#endif
#ifdef USE_WIFI_SCAN_RESULTS_LISTENERS
  wifi::global_wifi_component->add_scan_results_listener(this);
#endif

  esp_timer_create_args_t deadline{};
  deadline.callback = WacProvisioner::deadline_cb_;
  deadline.arg = this;
  deadline.name = "wac_deadline";
  esp_timer_create(&deadline, &this->deadline_timer_);
  esp_timer_create_args_t backstop{};
  backstop.callback = WacProvisioner::backstop_cb_;
  backstop.name = "wac_backstop";
  esp_timer_create(&backstop, &this->backstop_timer_);

  if (s_rtc.magic == RTC_MAGIC && esp_reset_reason() != ESP_RST_POWERON) {
    WacResult r;
    r.session = std::string(s_rtc.session, strnlen(s_rtc.session, sizeof(s_rtc.session)));
    r.stage = static_cast<WacStage>(s_rtc.stage);
    r.aborted_by_reboot = true;
    r.error = "rebooted during provisioning";
    r.log_tail.assign(s_rtc.log, std::min<size_t>(s_rtc.log_len, sizeof(s_rtc.log)));
    ESP_LOGW(TAG, "Previous provisioning session was interrupted by a reboot (stage %s)", stage_str(r.stage));
    this->pending_ = std::move(r);
    this->state_ = State::REPORTING;
    this->state_since_ = millis();
  }
  rtc_clear();
}

void WacProvisioner::dump_config() {
  ESP_LOGCONFIG(TAG,
                "WAC provisioner:\n"
                "  Scan interval: %" PRIu32 " s\n"
                "  Deadline: %" PRIu32 " s\n"
                "  Log cache: %u bytes",
                this->scan_interval_ms_ / 1000, this->deadline_ms_ / 1000, static_cast<unsigned>(this->log_cache_size_));
#ifndef USE_MDNS
  ESP_LOGCONFIG(TAG, "  mDNS disabled: falling back to the soft-AP gateway on port 80");
#endif
}

// ---- passive detection -------------------------------------------------------------------

void WacProvisioner::vendor_ie_cb_(void *ctx, wifi_vendor_ie_type_t type, const uint8_t sa[6],
                                   const vendor_ie_data_t *ie, int rssi) {
  // Runs in the Wi-Fi task.
  if (type != WIFI_VND_IE_TYPE_BEACON && type != WIFI_VND_IE_TYPE_PROBE_RESP)
    return;
  if (ie == nullptr || ie->length < 4 || ie->vendor_oui[0] != 0x00 || ie->vendor_oui[1] != 0xA0 ||
      ie->vendor_oui[2] != 0x40 || ie->vendor_oui_type != 0x00)
    return;
  protocol::Beacon beacon;
  if (!protocol::parse_beacon_payload(ie->payload, ie->length - 4, beacon))
    return;
  auto *self = static_cast<WacProvisioner *>(ctx);
  std::array<uint8_t, 6> bssid;
  memcpy(bssid.data(), sa, 6);
  std::lock_guard<std::mutex> lock(self->ie_lock_);
  for (auto &hit : self->ie_hits_) {
    if (hit.first == bssid) {
      hit.second.first = static_cast<int8_t>(rssi);
      return;
    }
  }
  if (self->ie_hits_.size() < 16)
    self->ie_hits_.emplace_back(bssid, std::make_pair(static_cast<int8_t>(rssi), std::move(beacon)));
}

void WacProvisioner::start_scan_() {
  if (!this->vendor_cb_registered_) {
    if (esp_wifi_set_vendor_ie_cb(WacProvisioner::vendor_ie_cb_, this) != ESP_OK)
      return;
    this->vendor_cb_registered_ = true;
  }
  {
    std::lock_guard<std::mutex> lock(this->ie_lock_);
    this->ie_hits_.clear();
  }
  wifi_scan_config_t cfg{};
  cfg.scan_type = WIFI_SCAN_TYPE_ACTIVE;
  if (esp_wifi_scan_start(&cfg, false) == ESP_OK) {
    this->scan_pending_ = true;
    ESP_LOGV(TAG, "Scanning for unconfigured accessories");
  }
}

#ifdef USE_WIFI_SCAN_RESULTS_LISTENERS
void WacProvisioner::on_wifi_scan_results(const wifi::wifi_scan_vector_t<wifi::WiFiScanResult> &results) {
  std::vector<std::pair<std::array<uint8_t, 6>, std::pair<int8_t, protocol::Beacon>>> hits;
  {
    std::lock_guard<std::mutex> lock(this->ie_lock_);
    hits.swap(this->ie_hits_);
  }
  this->scan_pending_ = false;
  const uint32_t now = millis();
  for (auto &hit : hits) {
    const wifi::WiFiScanResult *match = nullptr;
    for (const auto &res : results) {
      if (memcmp(res.get_bssid().data(), hit.first.data(), 6) == 0) {
        match = &res;
        break;
      }
    }
    if (match == nullptr || match->get_with_auth())
      continue;  // need the SSID to join, and WAC soft-APs are open
    Sighting s{};
    memcpy(s.bssid, hit.first.data(), 6);
    s.ssid = std::string(match->get_ssid().c_str(), match->get_ssid().size());
    s.channel = match->get_channel();
    s.rssi = hit.second.first;
    s.beacon = hit.second.second;
    s.seen_ms = now;
    bool replaced = false;
    for (auto &old : this->sightings_) {
      if (memcmp(old.bssid, s.bssid, 6) == 0) {
        old = s;
        replaced = true;
      }
    }
    if (!replaced)
      this->sightings_.push_back(s);

    WacAccessory acc;
    acc.wac_id = s.beacon.wac_id;
    acc.bssid = mac_str(s.bssid);
    acc.ssid = s.ssid;
    acc.channel = s.channel;
    acc.rssi = s.rssi;
    acc.name = s.beacon.name;
    acc.manufacturer = s.beacon.manufacturer;
    acc.model = s.beacon.model;
    ESP_LOGD(TAG, "Unconfigured accessory: %s (%s %s) RSSI %d", acc.name.c_str(), acc.manufacturer.c_str(),
             acc.model.c_str(), acc.rssi);
    this->found_cbs_.call(acc);
  }
  // Forget accessories not heard for a while (configured, reset, or moved away).
  for (auto it = this->sightings_.begin(); it != this->sightings_.end();) {
    it = now - it->seen_ms > SIGHTING_TTL_MS ? this->sightings_.erase(it) : it + 1;
  }
}
#endif

// ---- provisioning: main-loop side ---------------------------------------------------------

void WacProvisioner::refuse_(const std::string &session, WacStage stage, const char *error) {
  ESP_LOGW(TAG, "Provisioning refused: %s", error);
  WacResult r;
  r.session = session;
  r.stage = stage;
  r.error = error;
  this->result_cbs_.call(r);
}

void WacProvisioner::provision(const std::string &session, const std::string &bssid, const std::string &name,
                               const std::string &ssid, const std::string &psk) {
  if (this->state_ != State::IDLE)
    return this->refuse_(session, WacStage::JOIN, "a provisioning session is already running");
  if (this->deadline_timer_ == nullptr || this->backstop_timer_ == nullptr)
    return this->refuse_(session, WacStage::JOIN, "recovery timers unavailable; refusing to take the radio");
  uint8_t mac[6];
  if (!parse_mac(bssid, mac))
    return this->refuse_(session, WacStage::JOIN, "invalid BSSID");
  const Sighting *sighting = nullptr;
  for (const auto &s : this->sightings_) {
    if (memcmp(s.bssid, mac, 6) == 0)
      sighting = &s;
  }
  if (sighting == nullptr)
    return this->refuse_(session, WacStage::JOIN, "accessory not seen in a recent scan");

  auto *job = new Job();
  job->session = session;
  job->name = name.empty() ? sighting->beacon.name : name;
  if (ssid.empty()) {
    wifi::WiFiAP own = wifi::global_wifi_component->get_sta();
    job->target_ssid = std::string(own.get_ssid().c_str(), own.get_ssid().size());
    job->target_psk = std::string(own.get_password().c_str(), own.get_password().size());
#ifdef USE_WIFI_WPA2_EAP
    if (own.get_eap().has_value()) {
      delete job;
      return this->refuse_(session, WacStage::JOIN, "this node uses WPA2-Enterprise; pass an SSID and PSK");
    }
#endif
  } else {
    job->target_ssid = ssid;
    job->target_psk = psk;
  }
  if (job->target_ssid.empty() || job->target_ssid.size() > 32 ||
      (!job->target_psk.empty() && !protocol::valid_psk(job->target_psk))) {
    delete job;
    return this->refuse_(session, WacStage::JOIN, "invalid target SSID or PSK");
  }
  job->softap_ssid = sighting->ssid;
  memcpy(job->bssid, sighting->bssid, 6);
  job->channel = sighting->channel;
  job->wac_id = sighting->beacon.wac_id;

  ESP_LOGI(TAG, "Provisioning %s (%s) onto '%s'; this node goes offline for up to %" PRIu32 " s",
           job->name.c_str(), job->wac_id.c_str(), job->target_ssid.c_str(), this->deadline_ms_ / 1000);
  {
    std::unique_lock<std::mutex> lock(this->log_lock_, std::try_to_lock);
    if (lock.owns_lock())
      this->log_cache_.clear();
  }
  this->capturing_ = true;
  rtc_begin(session);
  this->job_ = job;
  this->task_done_ = false;
  this->deadline_hit_ = false;
  esp_timer_start_once(this->deadline_timer_, static_cast<uint64_t>(this->deadline_ms_) * 1000);
  esp_timer_start_once(this->backstop_timer_, static_cast<uint64_t>(this->deadline_ms_ + BACKSTOP_EXTRA_MS) * 1000);
  // Hand the radio over. This pauses the wifi component (and its reboot_timeout); the two
  // timers above bound how long that lasts.
  wifi::global_wifi_component->disable();
  this->state_ = State::HANDING_OVER;
  this->state_since_ = millis();
}

void WacProvisioner::loop() {
  const uint32_t now = millis();
  if (this->job_ != nullptr)
    s_rtc.stage = this->job_->stage.load();

  switch (this->state_) {
    case State::IDLE:
      if (!this->scan_pending_ && wifi::global_wifi_component->is_connected() && now > 30000 &&
          (this->last_scan_ == 0 || now - this->last_scan_ >= this->scan_interval_ms_)) {
        this->last_scan_ = now;
        this->start_scan_();
      }
      if (this->scan_pending_ && now - this->last_scan_ > 30000)
        this->scan_pending_ = false;  // results never came (e.g. roaming scan took over)
      break;

    case State::HANDING_OVER:
      if (xTaskCreate(WacProvisioner::session_task_, "wac_session", 8192, this, 5, &this->task_) != pdPASS) {
        this->job_->error = "could not start the session task";
        this->task_done_ = true;
        this->task_ = nullptr;
      }
      this->state_ = State::RUNNING;
      this->state_since_ = now;
      break;

    case State::RUNNING:
      if (this->task_done_) {
        this->task_ = nullptr;  // the task deleted itself
        this->state_ = State::RECLAIMING;
      } else if (this->deadline_hit_) {
        this->job_->abort = true;
        if (now - this->state_since_ > this->deadline_ms_ + ABANDON_GRACE_MS) {
          ESP_LOGE(TAG, "Session task stuck past the deadline; abandoning it");
          if (this->task_ != nullptr)
            vTaskDelete(this->task_);
          this->task_ = nullptr;
          this->job_->ok = false;
          if (this->job_->error.empty())
            this->job_->error = "deadline exceeded";
          this->state_ = State::RECLAIMING;
        }
      }
      break;

    case State::RECLAIMING:
      esp_wifi_disconnect();
      esp_wifi_stop();
      wifi::global_wifi_component->enable();
      esp_timer_stop(this->deadline_timer_);
      esp_timer_stop(this->backstop_timer_);  // reboot_timeout is armed again from here
      this->finish_job_();
      this->state_ = State::REPORTING;
      this->state_since_ = now;
      break;

    case State::REPORTING: {
      bool api_up = true;
#ifdef USE_API
      api_up = api::global_api_server != nullptr && api::global_api_server->is_connected();
#endif
      if (api_up || now - this->state_since_ > REPORT_WAIT_MS) {
        WacResult r = std::move(this->pending_);
        this->pending_ = WacResult{};
        this->state_ = State::IDLE;
        this->last_scan_ = now;  // let the network settle before the next scan
        this->report_(std::move(r));
      }
      break;
    }
  }
}

void WacProvisioner::finish_job_() {
  Job *job = this->job_;
  this->job_ = nullptr;
  this->capturing_ = false;
  WacResult r;
  r.session = job->session;
  r.stage = static_cast<WacStage>(job->stage.load());
  r.ok = job->ok;
  r.error = job->ok ? "" : (job->error.empty() ? "unknown error" : job->error);
  r.serial = job->serial;
  r.model = job->model;
  r.firmware = job->firmware;
  r.hardware = job->hardware;
  r.log_tail = this->log_tail_();
  uint8_t bssid[6];
  memcpy(bssid, job->bssid, 6);
  std::fill(job->target_psk.begin(), job->target_psk.end(), '\0');
  delete job;
  if (r.ok) {
    ESP_LOGI(TAG, "Accessory accepted the configuration (serial '%s')", r.serial.c_str());
    // It leaves the soft-AP now; forget it so it is not offered again.
    for (auto it = this->sightings_.begin(); it != this->sightings_.end();)
      it = memcmp(it->bssid, bssid, 6) == 0 ? this->sightings_.erase(it) : it + 1;
  } else {
    ESP_LOGW(TAG, "Provisioning failed at %s: %s", stage_str(r.stage), r.error.c_str());
  }
  this->pending_ = std::move(r);
}

void WacProvisioner::report_(WacResult result) {
  rtc_clear();
  this->result_cbs_.call(result);
}

std::string WacProvisioner::log_tail_() {
  // try_lock: a session task killed past the deadline could have died holding the lock.
  std::unique_lock<std::mutex> lock(this->log_lock_, std::try_to_lock);
  if (!lock.owns_lock())
    return "(log cache unavailable)";
  if (this->log_cache_.size() <= LOG_TAIL_REPORT)
    return this->log_cache_;
  return this->log_cache_.substr(this->log_cache_.size() - LOG_TAIL_REPORT);
}

void WacProvisioner::log_cb_(void *self_ptr, uint8_t level, const char *tag, const char *message, size_t len) {
  auto *self = static_cast<WacProvisioner *>(self_ptr);
  if (!self->capturing_ || level > ESPHOME_LOG_LEVEL_DEBUG)
    return;
  std::string line;
  line.reserve(len + 1);
  for (size_t i = 0; i < len; i++) {  // strip ANSI colour sequences
    if (message[i] == '\033') {
      while (i < len && message[i] != 'm')
        i++;
      continue;
    }
    line.push_back(message[i]);
  }
  line.push_back('\n');
  std::unique_lock<std::mutex> lock(self->log_lock_, std::try_to_lock);
  if (!lock.owns_lock())
    return;
  self->log_cache_ += line;
  if (self->log_cache_.size() > self->log_cache_size_)
    self->log_cache_.erase(0, self->log_cache_.size() - self->log_cache_size_);
  size_t n = std::min(self->log_cache_.size(), sizeof(s_rtc.log));
  memcpy(s_rtc.log, self->log_cache_.data() + self->log_cache_.size() - n, n);
  s_rtc.log_len = static_cast<uint16_t>(n);
}

void WacProvisioner::deadline_cb_(void *arg) {
  // esp_timer task: only flag; the main loop and the session task act on it.
  static_cast<WacProvisioner *>(arg)->deadline_hit_ = true;
}

void WacProvisioner::backstop_cb_(void *) {
  // The radio was not handed back to the wifi component long after the deadline.
  esp_restart();
}

// ---- provisioning: session task ------------------------------------------------------------

void WacProvisioner::session_task_(void *arg) {
  auto *self = static_cast<WacProvisioner *>(arg);
  run_session_(*self->job_);
  esp_wifi_disconnect();
  self->task_done_ = true;
  vTaskDelete(nullptr);
}

static void on_got_ip(void *arg, esp_event_base_t, int32_t, void *) {
  xEventGroupSetBits(static_cast<EventGroupHandle_t>(arg), BIT0);
}

void WacProvisioner::run_session_(Job &job) {
  auto set_stage = [&job](WacStage s) { job.stage = static_cast<uint8_t>(s); };
  auto fail = [&job](const char *error) { job.error = error; };

  // 1. Join the accessory's open soft-AP.
  set_stage(WacStage::JOIN);
  esp_netif_t *netif = esp_netif_get_handle_from_ifkey("WIFI_STA_DEF");
  if (netif == nullptr)
    return fail("no station interface");
  EventGroupHandle_t events = xEventGroupCreate();
  esp_event_handler_instance_t handler = nullptr;
  esp_event_handler_instance_register(IP_EVENT, IP_EVENT_STA_GOT_IP, on_got_ip, events, &handler);
  auto cleanup = [&]() {
    if (handler != nullptr)
      esp_event_handler_instance_unregister(IP_EVENT, IP_EVENT_STA_GOT_IP, handler);
    vEventGroupDelete(events);
  };

  wifi_config_t cfg{};
  memcpy(cfg.sta.ssid, job.softap_ssid.data(), std::min<size_t>(job.softap_ssid.size(), sizeof(cfg.sta.ssid)));
  memcpy(cfg.sta.bssid, job.bssid, 6);
  cfg.sta.bssid_set = true;
  cfg.sta.channel = job.channel;
  cfg.sta.threshold.authmode = WIFI_AUTH_OPEN;
  esp_netif_dhcpc_start(netif);  // in case the node normally uses a static IP
  if (esp_wifi_set_mode(WIFI_MODE_STA) != ESP_OK || esp_wifi_start() != ESP_OK ||
      esp_wifi_set_config(WIFI_IF_STA, &cfg) != ESP_OK || esp_wifi_connect() != ESP_OK) {
    cleanup();
    return fail("could not start the station on the accessory's soft-AP");
  }
  EventBits_t bits = xEventGroupWaitBits(events, BIT0, pdTRUE, pdTRUE, pdMS_TO_TICKS(JOIN_TIMEOUT_MS));
  cleanup();
  if ((bits & BIT0) == 0)
    return fail("could not join the accessory's soft-AP");
  if (job.abort)
    return fail("deadline exceeded");
  ESP_LOGI(TAG, "Joined %s", job.softap_ssid.c_str());

  // 2. Find its _mfi-config._tcp service.
  set_stage(WacStage::DISCOVER);
  char ip[16] = {0};
  uint16_t port = 0;
#ifdef USE_MDNS
  mdns_result_t *results = nullptr;
  if (mdns_query_ptr("_mfi-config", "_tcp", MDNS_TIMEOUT_MS, 4, &results) == ESP_OK) {
    for (mdns_result_t *r = results; r != nullptr && port == 0; r = r->next) {
      bool id_matches = true;
      for (size_t i = 0; i < r->txt_count; i++) {
        if (strcasecmp(r->txt[i].key, "deviceid") == 0 && r->txt[i].value != nullptr)
          id_matches = strcasecmp(r->txt[i].value, job.wac_id.c_str()) == 0;
      }
      for (mdns_ip_addr_t *a = r->addr; a != nullptr && id_matches; a = a->next) {
        if (a->addr.type == ESP_IPADDR_TYPE_V4) {
          esp_ip4addr_ntoa(&a->addr.u_addr.ip4, ip, sizeof(ip));
          port = r->port;
          break;
        }
      }
    }
    mdns_query_results_free(results);
  }
#endif
  if (port == 0) {
    esp_netif_ip_info_t info{};
    if (esp_netif_get_ip_info(netif, &info) != ESP_OK || info.gw.addr == 0)
      return fail("accessory's _mfi-config service not found");
    esp_ip4addr_ntoa(&info.gw, ip, sizeof(ip));
    port = 80;
    ESP_LOGW(TAG, "_mfi-config not found over mDNS; trying the gateway %s:80", ip);
  }
  if (job.abort)
    return fail("deadline exceeded");

  // 3. auth-setup.
  set_stage(WacStage::AUTH_SETUP);
  protocol::Session session;
  if (!session.begin(esp_rng, nullptr))
    return fail(session.error());
  http::Response resp;
  if (!http::post(ip, port, "auth-setup", session.auth_setup_request(), HTTP_TIMEOUT_MS, HTTP_MAX_BODY, resp))
    return fail("auth-setup request failed");
  if (resp.status != 200)
    return fail("auth-setup rejected by the accessory");
  if (!session.handle_auth_setup_response(resp.body.data(), resp.body.size()))
    return fail(session.error());
  if (job.abort)
    return fail("deadline exceeded");

  // 4. /config. From here on the accessory may hold the credentials: never retried elsewhere.
  protocol::Bytes body = session.config_request(job.name, job.target_ssid, job.target_psk);
  if (body.empty())
    return fail(session.error());
  set_stage(WacStage::CONFIG_SENT);
  if (!http::post(ip, port, "config", body, HTTP_TIMEOUT_MS, HTTP_MAX_BODY, resp))
    return fail("no answer to /config (the accessory may still apply it)");
  if (resp.status != 200)
    return fail("/config rejected by the accessory");
  if (!session.handle_config_response(resp.body.data(), resp.body.size()))
    return fail(session.error());
  job.serial = session.serial;
  job.model = session.model;
  job.firmware = session.firmware;
  job.hardware = session.hardware;
  job.ok = true;
  set_stage(WacStage::DONE);
}

}  // namespace esphome::wac_provisioner
