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

## How it is built

- Files: `wac_protocol.*` (TLV, beacon, X25519, KDF, stream, session: no Wi-Fi, no ESPHome),
  `wac_http.*` (blocking POST over BSD sockets; ESPHome excludes `esp_http_client` from IDF
  builds by default), `wac_provisioner.*` (scan, state machine, guards). The first two build
  on the host: `tests/esphome/test_protocol_host.py` runs them against aiowac's fake
  accessory.
- Detection: `esp_wifi_set_vendor_ie_cb` catches the WAC element during our own
  `esp_wifi_scan_start`; SSID and channel come from the wifi component's scan results via
  `WiFiScanResultsListener` (codegen calls `request_wifi_scan_results()` so non-configured
  networks are kept). ESPHome reads and frees the driver's AP records itself.
- Provisioning: main loop calls `wifi.disable()` (which stops the driver), a FreeRTOS task
  starts STA mode, joins the soft-AP by BSSID, queries `_mfi-config._tcp` with IDF mDNS
  (gateway:80 fallback), runs the exchange, stops the driver; the main loop calls
  `wifi.enable()` and reports once the API is connected again.

## Guards (all required)

1. Per-step timeouts inside the task (join 15 s, mDNS 4 s, each HTTP call 8 s).
2. `esp_timer` deadline (default 45 s) flags an abort; 10 s later the main loop kills the
   task if needed and takes the radio back.
3. Backstop `esp_timer` at deadline + 60 s calls `esp_restart()`. It replaces
   `reboot_timeout`, which `wifi.disable()` pauses, and is stopped only after `wifi.enable()`.
4. `RTC_NOINIT` marker with stage and log tail; at boot a set marker is reported as
   `aborted_by_reboot`.

## Traps

- `homeassistant.event` needs "Allow the device to perform Home Assistant actions" enabled.
- ESPHome's `mdns` component only advertises; browsing `_mfi-config._tcp` on the soft-AP
  needs IDF `mdns_query_ptr` without breaking ESPHome's responder.
- `wifi.disable()` returns from the wifi component's `loop()` before its `reboot_timeout`
  check, so that check is paused while we hold the radio (see guard 3).
- The firmware has not been run on hardware yet; the C++ outside `wac_protocol`/`wac_http`
  is only compile-checked in CI.
- Protocol code (`wac_session`, `wac_crypto`, `wac_tlv`) must stay free of Wi-Fi calls so
  it can be built and tested on the host against `aiowac`'s vectors.
