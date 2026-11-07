from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from aiohttp import web

from aiowac import (
    AiohttpTransport,
    WacHttpError,
    WacProtocolError,
    WacSession,
    WacStateError,
    WifiConfig,
    confirm_configured,
    provision,
)

from .fake_accessory import FakeAccessory

CONFIG = WifiConfig(name="Kitchen Plug", ssid="TestSSID", psk="testpassword")


@pytest.fixture
async def served() -> AsyncIterator[tuple[FakeAccessory, AiohttpTransport]]:
    fake = FakeAccessory()
    runner = web.AppRunner(fake.app())
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
    async with AiohttpTransport("127.0.0.1", port) as transport:
        yield fake, transport
    await runner.cleanup()


async def test_full_exchange_delivers_credentials(served):
    fake, transport = served
    result = await provision(transport, CONFIG)

    assert fake.received_config == {0x08: b"Kitchen Plug", 0x0C: b"TestSSID", 0x0B: b"testpassword"}
    assert result.auth.signature == fake.signature
    assert result.auth.certificate == fake.certificate
    assert result.info is not None
    assert (result.info.name, result.info.model, result.info.serial_number) == ("Test Plug", "TP-1", "SN0000000")
    assert (result.info.firmware_version, result.info.hardware_version) == ("1.2.3", "1")


async def test_open_network_sends_no_psk(served):
    fake, transport = served
    await provision(transport, WifiConfig(name="Plug", ssid="TestSSID"))
    assert fake.received_config is not None and 0x0B not in fake.received_config


async def test_empty_config_response_is_none(served):
    fake, transport = served
    fake.response_fields = []
    result = await provision(transport, CONFIG)
    assert result.info is None
    assert fake.received_config is not None


async def test_trailing_auth_data_rejected(served):
    fake, transport = served
    fake.trailing_auth_bytes = b"\x00"
    with pytest.raises(WacProtocolError):
        await provision(transport, CONFIG)


async def test_http_error_surfaces_status(served):
    fake, transport = served
    fake.config_status = 500
    with pytest.raises(WacHttpError) as err:
        await provision(transport, CONFIG)
    assert err.value.status == 500 and err.value.path == "config"


async def test_config_only_once_and_in_order(served):
    _, transport = served
    session = WacSession(transport)
    with pytest.raises(WacStateError):
        await session.configure(CONFIG)
    await session.auth_setup()
    await session.configure(CONFIG)
    with pytest.raises(WacStateError):
        await session.configure(CONFIG)


async def test_confirm_configured(served):
    fake, transport = served
    await confirm_configured(transport)
    assert fake.configured_called


@pytest.mark.parametrize("psk", ["short", "x" * 64, "y" * 65])
def test_invalid_psk_rejected(psk):
    with pytest.raises(ValueError):
        WifiConfig(name="Plug", ssid="TestSSID", psk=psk)


def test_hex_psk_accepted():
    WifiConfig(name="Plug", ssid="TestSSID", psk="ab" * 32)


def test_repr_hides_psk():
    assert "testpassword" not in repr(CONFIG)
