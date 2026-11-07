"""Finding `_mfi-config._tcp` services. Needs the optional `zeroconf` dependency."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from zeroconf import ServiceStateChange, Zeroconf
from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf

SERVICE_TYPE = "_mfi-config._tcp.local."


@dataclass(frozen=True)
class MfiConfigService:
    name: str
    device_id: str
    seed: int | None
    host: str
    port: int
    properties: dict[str, str]


async def find(
    device_id: str | None = None,
    *,
    timeout: float = 10.0,
    aiozc: AsyncZeroconf | None = None,
) -> MfiConfigService | None:
    """Return the first `_mfi-config._tcp` service (matching `device_id` if given)."""
    own = aiozc is None
    zc = aiozc or AsyncZeroconf()
    found: asyncio.Queue[str] = asyncio.Queue()

    def on_change(
        zeroconf: Zeroconf, service_type: str, name: str, state_change: ServiceStateChange
    ) -> None:
        if state_change is not ServiceStateChange.Removed:
            found.put_nowait(name)

    browser = AsyncServiceBrowser(zc.zeroconf, SERVICE_TYPE, handlers=[on_change])
    try:
        async with asyncio.timeout(timeout):
            while True:
                service = await _resolve(zc, await found.get())
                if service is None:
                    continue
                if device_id is None or service.device_id.upper() == device_id.upper():
                    return service
    except TimeoutError:
        return None
    finally:
        await browser.async_cancel()
        if own:
            await zc.async_close()


async def _resolve(zc: AsyncZeroconf, name: str) -> MfiConfigService | None:
    info = AsyncServiceInfo(SERVICE_TYPE, name)
    if not await info.async_request(zc.zeroconf, 3000):
        return None
    addresses = info.parsed_scoped_addresses()
    if not addresses or info.port is None:
        return None
    props = {
        k.decode(errors="replace").lower(): (v or b"").decode(errors="replace")
        for k, v in info.properties.items()
    }
    seed = props.get("seed")
    return MfiConfigService(
        name=name,
        device_id=props.get("deviceid", ""),
        seed=int(seed) if seed and seed.isdigit() else None,
        host=addresses[0],
        port=info.port,
        properties=props,
    )
