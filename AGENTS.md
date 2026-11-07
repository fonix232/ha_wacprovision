# ha_wacprovision: agent instructions

Puts Apple HomeKit Wi-Fi accessories onto a Wi-Fi network without an iPhone, by acting as the
configurator in Apple's Wireless Accessory Configuration (WAC) protocol. Home Assistant's
built-in HomeKit Device integration (`homekit_controller`) then pairs the accessory as usual.
This file is the single source of instructions for every AI tool. Claude Code reads it through
`CLAUDE.md` (`@AGENTS.md`), Gemini CLI through the `GEMINI.md` link, and the others natively.
Skills live in `.agents/skills` (linked as `.claude/skills`); Claude agents live in
`.claude/agents`.

## Layout

```
python/aiowac/                 WAC configurator library + CLI (PyPI package `aiowac`)
python/aiowac/src/aiowac/      library source: tlv, beacon, crypto, session, transport, cli
python/aiowac/tests/           pytest, including a fake accessory server
custom_components/wac_provision/  HA custom integration (HACS), domain `wac_provision`
custom_components/wac_provision/backends/  radios: esphome.py (ESPHome nodes), host.py (logic only)
components/wac_provisioner/    ESPHome external component (ESP32 only; uses IDF APIs)
tests/ha/                      HA flow and backend tests (pytest-homeassistant-custom-component)
tests/esphome/                 ESPHome test YAML, and a host build of the protocol code
scripts/version.py             the one version, stamped into pyproject.toml and manifest.json
.github/workflows/             ci.yml (every push and PR), release.yml (manual, publishes)
hacs.json                      HACS manifest (zip release)
.claude/agents/                Claude Code agents
.agents/                       shared skills, plans; local/ is ignored
```

## Hard rules (never break these)

- **Provisioning is only ever user-triggered.** Scanning is passive and only reports what it
  hears. Nothing sends Wi-Fi credentials to an accessory until the user taps "Add" in a
  flow. Auto-provisioning hands your PSK to any AP that advertises the right beacon.
- **Never retry `/config` on another radio once it has been sent.** The accessory may already
  hold the credentials and be joining; a second configurator races it. Retrying with the
  next-best radio is only allowed for failures before `/config` (join, discovery,
  auth-setup).
- **The WAC keystream is one continuous AES-128-CTR stream** per session: it decrypts the
  auth-setup signature first, then encrypts the `/config` request, then decrypts its
  response. Never create a fresh cipher per message.
- **An ESPHome node must always get back onto its normal Wi-Fi.** Every provisioning session
  is bounded by per-step timeouts, an `esp_timer` deadline that runs separately from the
  main loop, a backstop `esp_timer` that calls `esp_restart()`, and an `RTC_NOINIT`
  in-progress marker checked at boot. Handing the radio over uses `wifi.disable()`, which
  pauses ESPHome's `reboot_timeout`; the backstop timer replaces it until `wifi.enable()`.
  Never stop or extend the backstop while the wifi component is disabled.
- **v1 creates no config entry, device or entity per accessory.** The flow ends in
  `abort("wifi_provisioned")`; HomeKit Device's own discovery takes over. The only entry is
  the integration's single hub entry.
- **Public repository.** Never commit real SSIDs, PSKs, HomeKit setup codes, serial numbers,
  MAC addresses, device IDs or captured traffic. Use `00:11:22:33:44:55`, `TestSSID`,
  `testpassword`, `111-22-333` and `SN0000000` as placeholders. Captures go in `.agents/local/`.

## Design rules

- `aiowac` has no Home Assistant imports and no Wi-Fi control: callers join the soft-AP and
  hand it a transport. Both the HA host backend and the CLI use it.
- The ESPHome component owns the full WAC exchange on the ESP, because the ESP loses its HA
  connection while joined to the soft-AP; HA cannot proxy the exchange through it.
- Accessory-side device authentication (MFi certificate and signature check) is not
  implemented yet. `AuthSetup` keeps the certificate and decrypted signature so it can be
  added without changing callers.
- After the accessory joins the target network, the configurator POSTs `/configured` to its
  `_mfi-config._tcp` service on that network. In HA this is done by the integration (via
  HA's shared zeroconf instance) for every backend.
- Radio choice is automatic: best recent RSSI among the radios that heard the beacon.

## Build and test

```
cd python/aiowac && python -m pip install -e '.[test,cli]' && python -m pytest -q
python -m aiowac.cli decode-beacon <hex IE>
python -m aiowac.cli provision --ssid TestSSID          # while joined to the accessory's soft-AP; prompts for the PSK

# HA side, from the repo root, with `pytest-homeassistant-custom-component` and `-e python/aiowac`:
python -m pytest -q tests/ha

# ESPHome protocol code on the host (needs g++ and libmbedtls-dev), against the fake accessory:
python -m pytest -q tests/esphome

# ESPHome component (compiling needs the ESP-IDF toolchain from dl.espressif.com):
cd tests/esphome && esphome config test.esp32-idf.yaml && esphome compile test.esp32-idf.yaml

python3 scripts/version.py check
```

CI runs all of these. The firmware build only runs in CI when the sandbox cannot reach
Espressif's servers; push a branch and check the `esphome` job before merging C++ changes.

Real-device verification: factory-reset a HomeKit Wi-Fi accessory, join its open soft-AP
from a Linux machine (`nmcli dev wifi connect "<accessory SSID>"`), run `provision`, rejoin
the normal network and check that `_hap._tcp` shows the accessory with `sf=1`.

## Conventions

- One commit per finished, verified task. **No Co-Authored-By or other AI attribution
  trailers**, in commits, PRs or release notes. Push only when asked.
- Versions are only changed by `scripts/version.py` (the release workflow runs it). aiowac
  and the integration share one version; the manifest pins `aiowac==` that version.
- `aiowac` tests live in `python/aiowac/tests/`; new protocol behaviour needs a test against
  the fake accessory in `python/aiowac/tests/fake_accessory.py`, which reimplements the
  crypto on purpose so client bugs cannot cancel out. HA tests live in `tests/ha/`.
- Notes, logs, captures and reviews go under `.agents/local/` (ignored).

## Skills, agents, plans

- `.agents/skills/wac-protocol`: the WAC wire format, crypto and traps; read before touching
  `aiowac` or the ESPHome protocol code.
- `.agents/skills/ha-integration`: the HA flow, backends, `/configured` confirmation and the
  hand-off to HomeKit Device.
- `.agents/skills/esphome-component`: radio takeover, log cache and the recovery guards.
- `.agents/skills/wac-review`: the review checklist (credential safety, provisioning
  triggers, protocol correctness, recovery guarantees, HA scope, tests).
- `.claude/agents/wac-reviewer.md`: runs `wac-review` over the working tree's changes.
- `.agents/plans/roadmap.md`: v1 scope and what follows.
