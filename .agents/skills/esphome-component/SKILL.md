---
name: esphome-component
description: "The wac_provisioner ESPHome external component: passive WAC beacon scanning, the provisioning state machine, taking over the Wi-Fi radio, the log cache and recovery guards. Use when editing components/wac_provisioner."
---

# ESPHome component (`components/wac_provisioner`)

ESP32 only (uses IDF APIs: esp_wifi, esp_timer, mdns). One radio, one STA: the node drops its HA API connection for the ~20 s of a
provisioning session. That is accepted; the guards make it safe.

## Interface

- Passive scanning while connected (no dropout); beacons with the `00:A0:40` vendor IE are
  reported to HA as `esphome.wac_found` events with name, manufacturer, model, device ID,
  BSSID, channel and RSSI.
- API action `wac_provision(bssid, name, ssid = "", psk = "")`. Empty SSID/PSK = use the
  node's own Wi-Fi credentials.
- Result is held and sent after reconnect as `esphome.wac_result`: state reached, error,
  timings, the `/config` response fields, and the tail of the log cache.

## Guards (all required)

1. Per-step timeouts in the state machine.
2. Overall `esp_timer` deadline (~45 s) independent of the main loop; on expiry it aborts
   and restores normal Wi-Fi.
3. `RTC_NOINIT` in-progress marker written before disconnecting; at boot, a set marker means
   skip, use normal Wi-Fi, report `aborted_by_reboot` with the saved logs.
4. ESPHome's `wifi.reboot_timeout` must stay armed throughout.

## Traps

- `homeassistant.event` needs "Allow the device to perform Home Assistant actions" enabled.
- ESPHome's `mdns` component only advertises; browsing `_mfi-config._tcp` on the soft-AP
  needs IDF `mdns_query_ptr` without breaking ESPHome's responder.
- Verify how `wifi` disable/enable interacts with `esp_wifi_connect` before relying on it.
- Protocol code (`wac_session`, `wac_crypto`, `wac_tlv`) must stay free of Wi-Fi calls so
  it can be built and tested on the host against `aiowac`'s vectors.
