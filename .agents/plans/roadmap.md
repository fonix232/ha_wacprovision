# Roadmap

## v1: Wi-Fi provisioning only

- [ ] Go/no-go: provision one real HomeKit Wi-Fi accessory with `aiowac` from a Linux laptop.
- [x] `aiowac`: TLV8, beacon parsing, auth-setup, `/config`, `/configured`, CLI. Tested
      against a fake accessory.
- [ ] Cross-check `aiowac` against the Go reference client (BertoldVdb/WACResearch) once.
- [~] ESPHome `wac_provisioner`: passive scan + `esphome.wac_found` events. Written;
      compiled only in CI; not run on hardware.
- [~] ESPHome `wac_provisioner`: provisioning state machine, log cache, guards. Same.
- [x] HA `wac_provision`: hub entry, radio registry, discovery cards, provisioning flow
      ending in `abort("wifi_provisioned")` with the `/config` response shown and saved.
      Flow tests with pytest-homeassistant-custom-component.
- [x] ESPHome `wac_protocol.cpp` and `wac_http.cpp`, host-built and tested against the fake
      accessory.
- [~] HA host backend: `HostRadio` logic and tests done; the NetworkManager +
      wpa_supplicant D-Bus `WifiControl` and its registration are not.
- [x] CI, versioning (`scripts/version.py`) and the release workflow (PyPI + HACS zip).
- [ ] First release.

## v2

- Optional setup code / `X-HM://` payload; hand-off into HomeKit Device's flow through
  `homekit_controller_bridge.py`; exact matching via the `sh` TXT field.
- MFi certificate and signature verification of the accessory.
- Upstream: QR scanner selector (core + frontend); "pairing code provided" path in
  `homekit_controller`.

## Later

- ESPHome API actions now support responses (`supports_response`); the result could come
  back as an action response instead of the `esphome.wac_result` event, but the node is
  offline while provisioning, so the event may still be needed for the final result.

## Open questions

- Do current HomeKit firmwares accept this WAC v1 exchange, or a later variant tied to
  pair-setup?
- Does the WAC `deviceid` equal the HAP `id`?
- Does the Supervisor tolerate temporary NetworkManager connections on HA OS?
