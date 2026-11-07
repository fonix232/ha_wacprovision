"""Constants for wac_provision."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "wac_provision"

CONF_TARGET_SSID = "target_ssid"
CONF_TARGET_PSK = "target_psk"

# ESPHome events fired by the wac_provisioner component (homeassistant.event).
EVENT_ESPHOME_FOUND = "esphome.wac_found"
EVENT_ESPHOME_RESULT = "esphome.wac_result"

# A sighting older than this no longer counts when picking a radio.
SIGHTING_MAX_AGE = timedelta(minutes=10)
# Abort a discovery flow once no radio has heard the beacon for this long.
DISCOVERY_STALE_AFTER = timedelta(minutes=30)

PROVISION_TIMEOUT = 90.0  # radio leaves, provisions, comes back
CONFIRM_TIMEOUT = 60.0  # accessory shows up on the LAN as _mfi-config._tcp
