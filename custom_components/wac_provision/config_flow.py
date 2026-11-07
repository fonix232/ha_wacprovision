"""Hub setup, and the per-accessory discovery flow that provisions Wi-Fi.

The discovery flow never creates an entry: it ends in abort("wifi_provisioned") or
abort("provision_failed").
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType
from homeassistant.util import dt as dt_util

from .confirm import async_confirm
from .const import CONF_TARGET_PSK, CONF_TARGET_SSID, DOMAIN
from .models import ProvisionResult, ProvisionStage

if TYPE_CHECKING:
    from . import WacRuntime

HUB_TITLE = "HomeKit Wi-Fi Provisioning"


class WacProvisionConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._wac_id = ""
        self._beacon: dict[str, str] = {}
        self._task: asyncio.Task[ProvisionResult] | None = None
        self._radio = ""

    # --- hub -----------------------------------------------------------------

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if self._async_current_entries(include_ignore=False):
            return self.async_abort(reason="single_instance_allowed")
        if user_input is not None:
            return self.async_create_entry(
                title=HUB_TITLE, data={k: v for k, v in user_input.items() if v}
            )
        schema = vol.Schema(
            {
                vol.Optional(CONF_TARGET_SSID): str,
                vol.Optional(CONF_TARGET_PSK): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.PASSWORD)
                ),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema)

    # --- per accessory ---------------------------------------------------------

    async def async_step_integration_discovery(
        self, discovery_info: dict[str, Any]
    ) -> ConfigFlowResult:
        self._wac_id = discovery_info["wac_id"]
        await self.async_set_unique_id(self._wac_id)
        self._abort_if_unique_id_configured()  # honours "Ignore"
        self._beacon = {k: str(discovery_info.get(k, "")) for k in ("name", "manufacturer", "model")}
        self.context["title_placeholders"] = {
            "name": self._beacon["name"] or self._wac_id,
            "manufacturer": self._beacon["manufacturer"] or "HomeKit",
        }
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is None:
            return self.async_show_form(
                step_id="confirm",
                description_placeholders={**self._beacon, "wac_id": self._wac_id},
            )
        return await self.async_step_provision()

    async def async_step_provision(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if self._task is None:
            self._task = self.hass.async_create_task(self._async_run())
        if not self._task.done():
            return self.async_show_progress(
                step_id="provision",
                progress_action="provisioning",
                progress_task=self._task,
                description_placeholders={"radio": self._radio or "…"},
            )
        return self.async_show_progress_done(next_step_id="finish")

    async def async_step_finish(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        assert self._task is not None
        result = self._task.result()
        runtime = self._runtime()
        placeholders = {
            "name": self._beacon["name"] or self._wac_id,
            "serial": result.serial_number or "not reported",
            "model": result.model or self._beacon["model"] or "not reported",
            "firmware": result.firmware_version or "not reported",
            "error": result.error or "",
            "log_tail": result.log_tail or "",
        }
        if not result.ok:
            return self.async_abort(reason="provision_failed", description_placeholders=placeholders)

        if runtime is not None:
            runtime.sightings.forget(self._wac_id)
            saved = await runtime.store.async_load() or {}
            saved[self._wac_id] = {
                "name": placeholders["name"],
                "serial": result.serial_number,
                "model": result.model,
                "firmware": result.firmware_version,
                "hardware": result.hardware_version,
                "radio": self._radio,
                "provisioned_at": dt_util.utcnow().isoformat(),
            }
            await runtime.store.async_save(saved)
        persistent_notification.async_create(
            self.hass,
            (
                f"**{placeholders['name']}** joined Wi-Fi via {self._radio}.\n\n"
                f"Serial: {placeholders['serial']}  \nModel: {placeholders['model']}  \n"
                f"Firmware: {placeholders['firmware']}\n\n"
                "Finish pairing from the HomeKit Device discovery card."
            ),
            title="HomeKit accessory on Wi-Fi",
            notification_id=f"{DOMAIN}_{self._wac_id.replace(':', '').lower()}",
        )
        return self.async_abort(reason="wifi_provisioned", description_placeholders=placeholders)

    # --- internals -------------------------------------------------------------

    def _runtime(self) -> WacRuntime | None:
        entries = self._async_current_entries(include_ignore=False)
        hub = next((e for e in entries if e.unique_id is None), None)
        return getattr(hub, "runtime_data", None) if hub else None

    async def _async_run(self) -> ProvisionResult:
        runtime = self._runtime()
        if runtime is None:
            return ProvisionResult(False, ProvisionStage.JOIN, "integration is not set up")
        tried: set[str] = set()
        last: ProvisionResult | None = None
        while sightings := runtime.sightings.ranked(self._wac_id, exclude=tried):
            sighting = sightings[0]
            tried.add(sighting.radio_id)
            radio = runtime.radios.get(sighting.radio_id)
            if radio is None:
                continue
            self._radio = sighting.radio_id
            last = await radio.provision(sighting, self._beacon["name"] or "Accessory", runtime.target)
            if last.ok:
                if await async_confirm(self.hass, self._wac_id):
                    return last
                return ProvisionResult(
                    False,
                    ProvisionStage.CONFIG_SENT,
                    "accessory took the credentials but did not appear on the network; "
                    "check the password and signal, then factory-reset it and retry",
                    serial_number=last.serial_number,
                    model=last.model,
                    firmware_version=last.firmware_version,
                    log_tail=last.log_tail,
                )
            if not last.retryable_elsewhere:
                return last
        return last or ProvisionResult(False, ProvisionStage.JOIN, "no radio can hear this accessory")
