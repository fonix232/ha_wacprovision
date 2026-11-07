#include "wac_provisioner.h"

#include "esphome/core/log.h"

namespace esphome::wac_provisioner {

static const char *const TAG = "wac_provisioner";

// v1 skeleton: the radio takeover, state machine and guards are not implemented yet.
// Until they are, provision() reports a clean failure at the JOIN stage so HA never
// believes credentials were sent.

void WacProvisioner::setup() {}

void WacProvisioner::loop() {}

float WacProvisioner::get_setup_priority() const { return setup_priority::AFTER_WIFI; }

bool WacProvisioner::provision(const std::string &session, const std::string &bssid, const std::string &name,
                               const std::string &ssid, const std::string &psk) {
  ESP_LOGW(TAG, "provisioning not implemented yet");
  WacResult result;
  result.session = session;
  result.stage = WacStage::JOIN;
  result.error = "wac_provisioner: provisioning not implemented yet";
  this->result_cbs_.call(result);
  return false;
}

}  // namespace esphome::wac_provisioner
