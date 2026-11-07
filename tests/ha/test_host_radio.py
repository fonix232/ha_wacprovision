"""HostRadio logic against a fake WifiControl and aiowac's fake accessory."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from aiohttp import web

from custom_components.wac_provision.backends.host import Bss, HostRadio, JoinInfo, channel_for
from custom_components.wac_provision.models import ProvisionStage, WifiTarget

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "python" / "aiowac"))
from tests.fake_accessory import FakeAccessory  # noqa: E402

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
README_IE = bytes.fromhex("dd2400a0400007060011223344550002700301044e616d6502067761636c6962030454797065")
TARGET = WifiTarget("TestSSID", "testpassword")


class FakeControl:
    interface = "wlan1"

    def __init__(self, bsss: list[Bss], join_error: Exception | None = None) -> None:
        self.bsss = bsss
        self.join_error = join_error
        self.joined: list[tuple[bytes, str]] = []
        self.left = 0

    async def scan(self) -> list[Bss]:
        return self.bsss

    async def join_open(self, ssid: bytes, bssid: str, timeout: float) -> JoinInfo:
        if self.join_error:
            raise self.join_error
        self.joined.append((ssid, bssid))
        return JoinInfo(address="127.0.0.1", gateway="127.0.0.1")

    async def leave(self) -> None:
        self.left += 1


def wac_bss(privacy: bool = False) -> Bss:
    return Bss("00:11:22:33:44:56", b"Test Plug ABCD", 2437, -55, b"\x00\x04Plug" + README_IE, privacy)


@pytest.fixture
async def accessory(socket_enabled):
    fake = FakeAccessory()
    runner = web.AppRunner(fake.app())
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    yield fake, site._server.sockets[0].getsockname()[1]
    await runner.cleanup()


def test_channel_for() -> None:
    assert (channel_for(2412), channel_for(2437), channel_for(2484), channel_for(5180)) == (1, 6, 14, 36)


async def test_scan_finds_open_wac_soft_aps_only() -> None:
    radio = HostRadio(FakeControl([wac_bss(), wac_bss(privacy=True), Bss("00:11:22:33:44:57", b"Home", 2412, -40, b"", True)]))
    (s,) = await radio.scan(NOW)
    assert (s.device_id, s.radio_id, s.channel, s.rssi) == ("00:11:22:33:44:55", "host:wlan1", 6, -55)
    assert (s.name, s.manufacturer, s.model) == ("Name", "waclib", "Type")


async def test_provision_success(accessory) -> None:
    fake, port = accessory
    control = FakeControl([wac_bss()])

    async def discover(address, device_id):
        assert (address, device_id) == ("127.0.0.1", "00:11:22:33:44:55")
        return "127.0.0.1", port

    radio = HostRadio(control, discover)
    (s,) = await radio.scan(NOW)
    result = await radio.provision(s, "Kitchen Plug", TARGET)
    assert result.ok and result.stage is ProvisionStage.DONE
    assert result.serial_number == "SN0000000"
    assert control.joined == [(b"Test Plug ABCD", "00:11:22:33:44:56")]
    assert control.left == 1
    assert fake.received_config == {0x08: b"Kitchen Plug", 0x0C: b"TestSSID", 0x0B: b"testpassword"}


async def test_needs_a_target_network() -> None:
    radio = HostRadio(FakeControl([wac_bss()]))
    (s,) = await radio.scan(NOW)
    result = await radio.provision(s, "Plug", None)
    assert not result.ok and result.stage is ProvisionStage.JOIN and result.retryable_elsewhere


async def test_join_failure_is_retryable_and_leaves() -> None:
    control = FakeControl([wac_bss()], join_error=TimeoutError())
    radio = HostRadio(control)
    (s,) = await radio.scan(NOW)
    result = await radio.provision(s, "Plug", TARGET)
    assert result.stage is ProvisionStage.JOIN and result.retryable_elsewhere
    assert control.left == 1


async def test_config_failure_is_not_retryable(accessory) -> None:
    fake, port = accessory
    fake.config_status = 500

    async def discover(address, device_id):
        return "127.0.0.1", port

    radio = HostRadio(FakeControl([wac_bss()]), discover)
    (s,) = await radio.scan(NOW)
    result = await radio.provision(s, "Plug", TARGET)
    assert not result.ok and result.stage is ProvisionStage.CONFIG_SENT and not result.retryable_elsewhere
    assert "HTTP 500" in (result.error or "")
