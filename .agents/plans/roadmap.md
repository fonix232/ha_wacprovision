# Roadmap

## v1: Wi-Fi provisioning only

- [ ] Go/no-go: provision one real HomeKit Wi-Fi accessory with `aiowac` from a Linux laptop.
- [ ] `aiowac`: TLV8, beacon parsing, auth-setup, `/config`, `/configured`, CLI. Tested
      against a fake accessory.
- [ ] Cross-check `aiowac` against the Go reference client (BertoldVdb/WACResearch) once.
- [ ] ESPHome `wac_provisioner`: passive scan + `esphome.wac_found` events.
- [ ] ESPHome `wac_provisioner`: provisioning state machine, log cache, guards.
- [ ] HA `wac_provision`: hub entry, radio registry, discovery cards, provisioning flow
      ending in `abort("wifi_provisioned")` with the `/config` response shown and saved.
- [ ] HA host backend: NetworkManager + wpa_supplicant D-Bus radio on a spare interface.
- [ ] HACS release.

## v2

- Optional setup code / `X-HM://` payload; hand-off into HomeKit Device's flow through
  `homekit_controller_bridge.py`; exact matching via the `sh` TXT field.
- MFi certificate and signature verification of the accessory.
- Upstream: QR scanner selector (core + frontend); "pairing code provided" path in
  `homekit_controller`.

## Open questions

- Do current HomeKit firmwares accept this WAC v1 exchange, or a later variant tied to
  pair-setup?
- Does the WAC `deviceid` equal the HAP `id`?
- Does the Supervisor tolerate temporary NetworkManager connections on HA OS?
