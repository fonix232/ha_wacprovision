"""Flow tests: hub setup, discovery cards, provisioning outcomes and the retry rule."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.wac_provision.backends.esphome import EsphomeRadio
from custom_components.wac_provision.const import DOMAIN, EVENT_ESPHOME_FOUND
from custom_components.wac_provision.models import ProvisionResult, ProvisionStage

WAC_ID = "00:11:22:33:44:55"


async def setup_hub(hass: HomeAssistant, data: dict | None = None) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title="HomeKit Wi-Fi Provisioning", data=data or {})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def found(hass: HomeAssistant, node: str, rssi: int) -> None:
    hass.bus.async_fire(
        EVENT_ESPHOME_FOUND,
        {
            "node": node,
            "wac_id": WAC_ID,
            "bssid": "00:11:22:33:44:56",
            "channel": "6",
            "rssi": str(rssi),
            "name": "Test Plug",
            "manufacturer": "Acme",
            "model": "TP-1",
        },
    )


def our_flows(hass: HomeAssistant) -> list[dict]:
    return hass.config_entries.flow.async_progress_by_handler(DOMAIN)


async def run_add(hass: HomeAssistant) -> dict:
    """Press Add on the discovery card and return the flow's final result.

    HA starts the provisioning task eagerly, so with instant mocks the progress step can
    finish within the same call.
    """
    (flow,) = our_flows(hass)
    result = await hass.config_entries.flow.async_configure(flow["flow_id"], {})
    while result["type"] in (FlowResultType.SHOW_PROGRESS, FlowResultType.SHOW_PROGRESS_DONE):
        await hass.async_block_till_done()
        result = await hass.config_entries.flow.async_configure(flow["flow_id"])
    assert result["type"] is FlowResultType.ABORT
    return result


async def test_user_step_creates_single_hub(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    with patch("custom_components.wac_provision.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"target_ssid": "TestSSID", "target_psk": "testpassword"}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {"target_ssid": "TestSSID", "target_psk": "testpassword"}

    again = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert again["type"] is FlowResultType.ABORT and again["reason"] == "single_instance_allowed"


async def test_sighting_creates_one_discovery_card(hass: HomeAssistant) -> None:
    await setup_hub(hass)
    found(hass, "kitchen-esp", -60)
    found(hass, "hall-esp", -50)
    await hass.async_block_till_done()
    flows = our_flows(hass)
    assert len(flows) == 1
    assert flows[0]["context"]["unique_id"] == WAC_ID
    assert flows[0]["step_id"] == "confirm"
    assert flows[0]["context"]["title_placeholders"] == {"name": "Test Plug", "manufacturer": "Acme"}


async def test_success_creates_nothing_and_records_details(hass: HomeAssistant) -> None:
    entry = await setup_hub(hass)
    found(hass, "kitchen-esp", -60)
    await hass.async_block_till_done()
    ok = ProvisionResult(True, ProvisionStage.DONE, serial_number="SN0000000", model="TP-1", firmware_version="1.2.3")
    with (
        patch.object(EsphomeRadio, "provision", AsyncMock(return_value=ok)) as provision,
        patch("custom_components.wac_provision.config_flow.async_confirm", AsyncMock(return_value=True)) as confirm,
    ):
        result = await run_add(hass)

    assert result["reason"] == "wifi_provisioned"
    assert result["description_placeholders"]["serial"] == "SN0000000"
    provision.assert_awaited_once()
    sighting, name, target = provision.await_args.args
    assert (sighting.radio_id, name, target) == ("esphome:kitchen-esp", "Test Plug", None)
    confirm.assert_awaited_once_with(hass, WAC_ID)
    assert our_flows(hass) == []
    assert hass.config_entries.async_entries(DOMAIN) == [entry]  # still only the hub
    saved = await entry.runtime_data.store.async_load()
    assert saved[WAC_ID]["serial"] == "SN0000000"
    assert saved[WAC_ID]["radio"] == "esphome:kitchen-esp"
    notifications = hass.data["persistent_notification"]
    assert any("SN0000000" in n["message"] for n in notifications.values())


async def test_best_rssi_first_and_retry_before_config(hass: HomeAssistant) -> None:
    await setup_hub(hass, {"target_ssid": "TestSSID", "target_psk": "testpassword"})
    found(hass, "far-esp", -80)
    found(hass, "near-esp", -40)
    await hass.async_block_till_done()
    calls: list[str] = []

    async def provision(self: EsphomeRadio, sighting, name, target):
        calls.append(self.node)
        assert target.ssid == "TestSSID"
        if self.node == "near-esp":
            return ProvisionResult(False, ProvisionStage.AUTH_SETUP, "auth-setup rejected")
        return ProvisionResult(True, ProvisionStage.DONE)

    with (
        patch.object(EsphomeRadio, "provision", provision),
        patch("custom_components.wac_provision.config_flow.async_confirm", AsyncMock(return_value=True)),
    ):
        result = await run_add(hass)
    assert result["reason"] == "wifi_provisioned"
    assert calls == ["near-esp", "far-esp"]


async def test_no_retry_once_config_was_sent(hass: HomeAssistant) -> None:
    entry = await setup_hub(hass)
    found(hass, "far-esp", -80)
    found(hass, "near-esp", -40)
    await hass.async_block_till_done()
    failed = ProvisionResult(False, ProvisionStage.CONFIG_SENT, "no answer to /config")
    with (
        patch.object(EsphomeRadio, "provision", AsyncMock(return_value=failed)) as provision,
        patch("custom_components.wac_provision.config_flow.async_confirm", AsyncMock(return_value=True)) as confirm,
    ):
        result = await run_add(hass)
    assert result["reason"] == "provision_failed"
    assert result["description_placeholders"]["error"] == "no answer to /config"
    provision.assert_awaited_once()
    confirm.assert_not_awaited()
    assert our_flows(hass) == []
    assert not await entry.runtime_data.store.async_load()


async def test_accessory_never_appears_is_a_failure(hass: HomeAssistant) -> None:
    entry = await setup_hub(hass)
    found(hass, "kitchen-esp", -60)
    await hass.async_block_till_done()
    ok = ProvisionResult(True, ProvisionStage.DONE, serial_number="SN0000000")
    with (
        patch.object(EsphomeRadio, "provision", AsyncMock(return_value=ok)),
        patch("custom_components.wac_provision.config_flow.async_confirm", AsyncMock(return_value=False)),
    ):
        result = await run_add(hass)
    assert result["reason"] == "provision_failed"
    assert "did not appear on the network" in result["description_placeholders"]["error"]
    assert result["description_placeholders"]["serial"] == "SN0000000"
    assert our_flows(hass) == []
    assert not await entry.runtime_data.store.async_load()


async def test_ignored_accessory_stays_ignored(hass: HomeAssistant) -> None:
    await setup_hub(hass)
    found(hass, "kitchen-esp", -60)
    await hass.async_block_till_done()
    (flow,) = our_flows(hass)
    await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_IGNORE},
        data={"unique_id": WAC_ID, "title": "Test Plug"},
    )
    await hass.async_block_till_done()
    assert our_flows(hass) == []
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_INTEGRATION_DISCOVERY},
        data={"wac_id": WAC_ID, "name": "Test Plug", "manufacturer": "Acme", "model": "TP-1"},
    )
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "already_configured"
