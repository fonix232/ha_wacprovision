"""Send /configured to the accessory once it shows up on the LAN."""

from __future__ import annotations

from aiowac import AiohttpTransport, WacError, confirm_configured
from aiowac.discovery import find
from homeassistant.components import zeroconf
from homeassistant.core import HomeAssistant

from .const import CONFIRM_TIMEOUT


async def async_confirm(hass: HomeAssistant, device_id: str) -> bool:
    """Return True when the accessory joined the LAN and acknowledged /configured."""
    aiozc = await zeroconf.async_get_async_instance(hass)
    service = await find(device_id, timeout=CONFIRM_TIMEOUT, aiozc=aiozc)
    if service is None:
        return False
    try:
        async with AiohttpTransport(service.host, service.port) as transport:
            await confirm_configured(transport)
    except WacError:
        return False
    return True
