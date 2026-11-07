"""Unit tests for the HA-side logic that needs no running Home Assistant."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from custom_components.wac_provision.backends.esphome import _parse_result
from custom_components.wac_provision.models import ProvisionResult, ProvisionStage, Sighting
from custom_components.wac_provision.registry import SightingRegistry

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def sighting(radio: str, rssi: int, age: timedelta = timedelta()) -> Sighting:
    return Sighting("00:11:22:33:44:55", radio, "00:11:22:33:44:56", 6, rssi, "Plug", "Acme", "P1", NOW - age)


async def test_ranked_best_rssi_first_and_drops_stale():
    reg = SightingRegistry(lambda: NOW)
    assert reg.add(sighting("esphome:a", -70)) is True
    assert reg.add(sighting("esphome:b", -50)) is False
    reg.add(sighting("esphome:c", -40, age=timedelta(hours=1)))
    assert [s.radio_id for s in reg.ranked("00:11:22:33:44:55")] == ["esphome:b", "esphome:a"]
    assert [s.radio_id for s in reg.ranked("00:11:22:33:44:55", exclude={"esphome:b"})] == ["esphome:a"]


async def test_only_pre_config_failures_retry_elsewhere():
    for stage in (ProvisionStage.JOIN, ProvisionStage.DISCOVER, ProvisionStage.AUTH_SETUP):
        assert ProvisionResult(False, stage).retryable_elsewhere
    assert not ProvisionResult(False, ProvisionStage.CONFIG_SENT).retryable_elsewhere
    assert not ProvisionResult(True, ProvisionStage.DONE).retryable_elsewhere


async def test_esphome_result_strings():
    assert _parse_result({"ok": "false", "stage": "join"}).ok is False
    assert _parse_result({"ok": "true", "stage": "done"}).ok is True
    assert _parse_result({"ok": "false", "stage": "garbage"}).stage is ProvisionStage.CONFIG_SENT
