"""ESPHome nodes running the wac_provisioner component as radios.

Event payloads use `wac_id` for the accessory's WAC device ID, because HA's ESPHome
integration adds its own `device_id` (the HA device) to esphome.* events.

esphome.wac_found:  node, wac_id, bssid, channel, rssi, name, manufacturer, model
esphome.wac_result: node, session, ok, stage, error, serial, model, firmware, hardware, log_tail

All values arrive as strings (ESPHome's homeassistant.event). `stage` is one of the
ProvisionStage values: join, discover, auth_setup, config_sent, done.
"""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import Callable
from datetime import datetime

from homeassistant.core import Event, HomeAssistant, callback

from ..const import EVENT_ESPHOME_FOUND, EVENT_ESPHOME_RESULT, PROVISION_TIMEOUT
from ..models import ProvisionResult, ProvisionStage, Sighting, WifiTarget


def radio_id_for(node: str) -> str:
    return f"esphome:{node}"


def parse_found(event: Event, now: datetime) -> Sighting:
    data = event.data
    return Sighting(
        device_id=str(data["wac_id"]).upper(),
        radio_id=radio_id_for(str(data["node"])),
        bssid=str(data["bssid"]).upper(),
        channel=int(data["channel"]),
        rssi=int(data["rssi"]),
        name=str(data.get("name", "")),
        manufacturer=str(data.get("manufacturer", "")),
        model=str(data.get("model", "")),
        seen=now,
    )


class EsphomeRadio:
    """Calls `esphome.<node>_wac_provision` and waits for the matching wac_result event.

    The node drops off the network while provisioning, so the result arrives as an event
    after it reconnects, correlated by `session`.
    """

    def __init__(self, hass: HomeAssistant, node: str) -> None:
        self.hass = hass
        self.node = node
        self.radio_id = radio_id_for(node)

    async def provision(self, sighting: Sighting, name: str, target: WifiTarget | None) -> ProvisionResult:
        session = secrets.token_hex(4)
        result: asyncio.Future[ProvisionResult] = self.hass.loop.create_future()

        @callback
        def on_result(event: Event) -> None:
            if event.data.get("node") == self.node and event.data.get("session") == session:
                if not result.done():
                    result.set_result(_parse_result(event.data))

        unsub = self.hass.bus.async_listen(EVENT_ESPHOME_RESULT, on_result)
        try:
            await self.hass.services.async_call(
                "esphome",
                f"{self.node.replace('-', '_')}_wac_provision",
                {
                    "session": session,
                    "bssid": sighting.bssid,
                    "name": name,
                    "ssid": target.ssid if target else "",
                    "psk": (target.psk or "") if target else "",
                },
                blocking=False,
            )
            async with asyncio.timeout(PROVISION_TIMEOUT):
                return await result
        except TimeoutError:
            # Unknown how far it got: treat as possibly configured, never retry elsewhere.
            return ProvisionResult(False, ProvisionStage.CONFIG_SENT, "node did not report back")
        finally:
            unsub()


def subscribe_found(hass: HomeAssistant, on_sighting: Callable[[Sighting], None]) -> Callable[[], None]:
    @callback
    def handle(event: Event) -> None:
        on_sighting(parse_found(event, datetime.now().astimezone()))

    return hass.bus.async_listen(EVENT_ESPHOME_FOUND, handle)


def _parse_result(data: dict) -> ProvisionResult:
    try:
        stage = ProvisionStage(str(data.get("stage")))
    except ValueError:
        stage = ProvisionStage.CONFIG_SENT  # unknown: assume the worst, never retry elsewhere
    return ProvisionResult(
        ok=str(data.get("ok", "")).lower() in ("true", "1"),  # event values arrive as strings
        stage=stage,
        error=data.get("error") or None,
        serial_number=str(data.get("serial", "")),
        model=str(data.get("model", "")),
        firmware_version=str(data.get("firmware", "")),
        hardware_version=str(data.get("hardware", "")),
        log_tail=str(data.get("log_tail", "")),
    )
