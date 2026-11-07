# HomeKit Wi-Fi Provisioning (WAC) for Home Assistant

Put HomeKit Wi-Fi accessories onto your Wi-Fi **without an iPhone**. This acts as the
configurator in Apple's Wireless Accessory Configuration (WAC) protocol, the step the Home
app normally does. Once the accessory is on your network, Home Assistant's built-in
**HomeKit Device** integration discovers it and you pair it there as usual.

> **Status: early v1, not usable yet.** The `aiowac` library and CLI work against a test
> accessory; the ESPHome provisioning state machine and the host Wi-Fi radio are not
> implemented. It is not yet known whether current HomeKit firmware accepts this exchange.

## How it works

1. A **radio** hears an unconfigured accessory's soft-AP beacon: an ESPHome node running the
   `wac_provisioner` component, or (planned) the Home Assistant host's own spare Wi-Fi.
2. The accessory shows up as a discovered device. Nothing is sent until you tap **Add**.
3. The radio with the best signal joins the accessory's open soft-AP, runs the WAC exchange
   (X25519 key agreement, then your SSID and password encrypted with AES-CTR) and returns to
   your network.
4. Home Assistant finds the accessory on your LAN, confirms the setup, and shows the serial,
   model and firmware it reported. No device or entity is created by this integration.
5. HomeKit Device shows its own discovery card; enter the setup code there.

You can provision without a setup code, for example to read a serial number so the
manufacturer can issue a new code.

## Repository

| Path | What |
| --- | --- |
| `python/aiowac/` | WAC configurator library and CLI (PyPI: `aiowac`) |
| `custom_components/wac_provision/` | Home Assistant integration (HACS) |
| `components/wac_provisioner/` | ESPHome external component (ESP32) |

## Testing a real accessory with the CLI

From a Linux machine with Wi-Fi:

```sh
pip install 'aiowac[cli]'            # or: pip install -e 'python/aiowac[cli]'
nmcli dev wifi connect "<accessory soft-AP SSID>"
aiowac provision --ssid "<your SSID>"   # prompts for the password
# rejoin your own network when asked; the CLI then sends /configured
```

If it works, the accessory appears as `_hap._tcp` with `sf=1` and HomeKit Device offers it.
Please open an issue with the result either way (brand, model, firmware; no serials or MACs).

## ESPHome radio

```yaml
external_components:
  - source: github://fonix232/ha_wacprovision
    components: [wac_provisioner]

wac_provisioner:
  id: wac
  on_found:
    - homeassistant.event:
        event: esphome.wac_found
        data:
          node: !lambda "return App.get_name();"
          wac_id: !lambda "return acc.wac_id;"
          bssid: !lambda "return acc.bssid;"
          channel: !lambda "return to_string(acc.channel);"
          rssi: !lambda "return to_string(acc.rssi);"
          name: !lambda "return acc.name;"
          manufacturer: !lambda "return acc.manufacturer;"
          model: !lambda "return acc.model;"
  on_result:
    - homeassistant.event:
        event: esphome.wac_result
        data:
          node: !lambda "return App.get_name();"
          session: !lambda "return res.session;"
          ok: !lambda 'return res.ok ? "true" : "false";'
          stage: !lambda "return esphome::wac_provisioner::stage_str(res.stage);"
          error: !lambda "return res.error;"
          serial: !lambda "return res.serial;"
          model: !lambda "return res.model;"
          firmware: !lambda "return res.firmware;"
          hardware: !lambda "return res.hardware;"
          log_tail: !lambda "return res.log_tail;"

api:
  actions:
    - action: wac_provision
      variables:
        session: string
        bssid: string
        name: string
        ssid: string
        psk: string
      then:
        - lambda: "id(wac).provision(session, bssid, name, ssid, psk);"
```

In Home Assistant, enable **Allow the device to perform Home Assistant actions** for the
node, or the events never arrive.

## Security

The WAC accessory does not authenticate the configurator, and this integration does not
yet verify the accessory's MFi certificate. A rogue access point imitating an accessory
could receive your Wi-Fi password if you tap **Add** on it. That is why provisioning is
never automatic. Consider provisioning onto a separate IoT network.

## Credits

Protocol details from [BertoldVdb/WACResearch](https://github.com/BertoldVdb/WACResearch)
(MIT). Not affiliated with Apple.
