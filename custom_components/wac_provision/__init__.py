"""HomeKit Wi-Fi provisioning over Apple WAC.

v1: unconfigured accessories heard by any radio appear as discovery cards. "Add" sends the
target network's credentials through the best radio and confirms over the LAN. Nothing is
created per accessory; HomeKit Device's own discovery takes over afterwards.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from homeassistant.config_entries import SOURCE_INTEGRATION_DISCOVERY, ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import discovery_flow
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .backends.esphome import EsphomeRadio, subscribe_found
from .const import CONF_TARGET_PSK, CONF_TARGET_SSID, DISCOVERY_STALE_AFTER, DOMAIN
from .models import Sighting, WifiTarget
from .provisioner import RadioRegistry
from .registry import SightingRegistry

STORAGE_VERSION = 1


@dataclass
class WacRuntime:
    sightings: SightingRegistry
    radios: RadioRegistry
    target: WifiTarget | None
    store: Store[dict[str, dict[str, str]]]


type WacConfigEntry = ConfigEntry[WacRuntime]


async def async_setup_entry(hass: HomeAssistant, entry: WacConfigEntry) -> bool:
    target = None
    if ssid := entry.data.get(CONF_TARGET_SSID):
        target = WifiTarget(ssid, entry.data.get(CONF_TARGET_PSK) or None)
    runtime = WacRuntime(
        sightings=SightingRegistry(dt_util.now),
        radios=RadioRegistry(),
        target=target,
        store=Store(hass, STORAGE_VERSION, f"{DOMAIN}.provisioned"),
    )
    entry.runtime_data = runtime

    @callback
    def on_sighting(sighting: Sighting) -> None:
        if runtime.radios.get(sighting.radio_id) is None:
            node = sighting.radio_id.removeprefix("esphome:")
            runtime.radios.register(EsphomeRadio(hass, node))
        if runtime.sightings.add(sighting):
            discovery_flow.async_create_flow(
                hass,
                DOMAIN,
                context={"source": SOURCE_INTEGRATION_DISCOVERY},
                data={
                    "wac_id": sighting.device_id,
                    "name": sighting.name,
                    "manufacturer": sighting.manufacturer,
                    "model": sighting.model,
                },
            )

    @callback
    def prune_stale(now: datetime) -> None:
        for flow in hass.config_entries.flow.async_progress_by_handler(DOMAIN):
            wac_id = flow["context"].get("unique_id")
            if flow["step_id"] != "confirm" or wac_id is None:
                continue  # never abort a flow that is provisioning
            last = runtime.sightings.last_seen(wac_id)
            if last is None or now - last > DISCOVERY_STALE_AFTER:
                hass.config_entries.flow.async_abort(flow["flow_id"])
                runtime.sightings.forget(wac_id)

    entry.async_on_unload(subscribe_found(hass, on_sighting))
    entry.async_on_unload(async_track_time_interval(hass, prune_stale, timedelta(minutes=5)))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: WacConfigEntry) -> bool:
    return True
