---
name: ha-integration
description: "The wac_provision Home Assistant custom integration: discovery cards, the provisioning flow, radios (ESPHome proxies and the host's own Wi-Fi), /configured confirmation and hand-off to HomeKit Device. Use when editing custom_components/wac_provision."
---

# Home Assistant integration (`custom_components/wac_provision`)

## Shape (v1)

- One hub config entry (`single_config_entry`), holding the target network for radios that
  do not have their own credentials (the host backend). ESPHome radios default to their own.
- Radios report sightings into `registry.py`: `device_id -> {radio_id: (rssi, last_seen)}`.
- A new device ID starts a flow with `SOURCE_INTEGRATION_DISCOVERY`, `unique_id` = WAC
  device ID, title from the beacon (name, manufacturer). Ignore must stick.
- "Add" runs: pick best radio -> provision (progress step) -> confirm via `/configured` on
  the LAN -> `abort("wifi_provisioned")` with the `/config` response (serial, model,
  firmware) in the description placeholders and a persistent notification.
- When no radio has heard the beacon for a while, abort the in-progress discovery flow.
- HomeKit Device then shows its own discovery card for the unpaired `_hap._tcp` advert.

## Traps

- Never create a config entry per accessory in v1.
- Retry on the next radio only for failures before `/config` was sent.
- Use HA's shared zeroconf instance (`zeroconf.async_get_async_instance`) to find
  `_mfi-config._tcp` after the accessory joins; do not open a second responder on the LAN.
- The host backend must not use the interface carrying HA's default route unless the user
  opted in (v1: refuse).
- `homekit_controller` internals (step names, `pairing_code`) are only touched from
  `homekit_controller_bridge.py`, which does not exist until v2.
