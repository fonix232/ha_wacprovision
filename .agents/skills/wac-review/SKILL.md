---
name: wac-review
description: "Review checklist for ha_wacprovision changes - credential leaks, unsafe provisioning triggers, WAC protocol mistakes, ESPHome recovery gaps, HA scope and missing tests. Use when reviewing a diff or before committing changes to aiowac, the HA integration or the ESPHome component."
---

# Reviewing ha_wacprovision changes

Read `AGENTS.md` and `.agents/skills/wac-protocol/SKILL.md` first. Review unstaged changes
(`git diff`), staged changes (`git diff --cached`) and the contents of relevant untracked
files. Check, in this order, and report only real findings with file and line:

1. **Credentials and identifiers**: a real SSID, PSK, setup code, serial, MAC or device ID
   in code, tests, fixtures or docs; credentials written to logs at any level; PSKs stored
   anywhere other than the hub config entry. Placeholders are `00:11:22:33:44:55`,
   `TestSSID`, `testpassword`, `111-22-333`, `SN0000000`.
2. **Provisioning triggers**: any path that sends `/config` without an explicit user
   confirmation in a flow; retrying on another radio after `/config` was sent; a radio
   result whose stage is unknown being treated as retryable.
3. **Protocol correctness**: a new CTR stream per message, wrong order of stream use,
   missing TLV fragmentation on encode or merge on decode, unbounded reads of HTTP bodies,
   trailing data accepted after the auth-setup signature.
4. **ESPHome recovery**: a provisioning path not covered by the deadline timer, the backstop
   restart timer or the boot marker; the backstop stopped before `wifi.enable()`; blocking
   calls without timeouts, or blocking calls on the main loop instead of the session task.
5. **HA scope**: v1 creating config entries, devices or entities per accessory;
   `homekit_controller` internals referenced outside `homekit_controller_bridge.py`;
   `single_config_entry` in the manifest (it aborts discovery flows).
6. **Tests**: protocol changes without a test against
   `python/aiowac/tests/fake_accessory.py`.

Be terse. Findings first, most severe first; no praise.
