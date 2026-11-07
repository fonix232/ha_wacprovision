"""aiohttp-based transport for WacSession."""

from __future__ import annotations

from types import TracebackType

import aiohttp

from .exceptions import WacError, WacHttpError

MAX_RESPONSE = 8192
DEFAULT_TIMEOUT = 10.0


class AiohttpTransport:
    """POSTs to `http://<host>:<port>/<path>` without keep-alive, like the reference client.

    `local_addr` binds outgoing connections to an interface address, for hosts where the
    soft-AP is reached through a secondary interface.
    """

    def __init__(
        self,
        host: str,
        port: int,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        local_addr: tuple[str, int] | None = None,
    ) -> None:
        bracketed = f"[{host}]" if ":" in host else host
        self._base = f"http://{bracketed}:{port}/"
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._local_addr = local_addr
        self._session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> AiohttpTransport:
        connector = aiohttp.TCPConnector(force_close=True, local_addr=self._local_addr)
        self._session = aiohttp.ClientSession(connector=connector, timeout=self._timeout)
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def post(self, path: str, body: bytes) -> bytes:
        if self._session is None:
            raise RuntimeError("AiohttpTransport must be used as an async context manager")
        headers = {"Content-Type": "application/octet-stream", "Connection": "close"}
        try:
            async with self._session.post(self._base + path, data=body, headers=headers) as resp:
                if resp.status != 200:
                    raise WacHttpError(path, resp.status)
                data = await resp.content.read(MAX_RESPONSE + 1)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise WacError(f"POST {path} failed: {err}") from err
        if len(data) > MAX_RESPONSE:
            raise WacError(f"POST {path} response larger than {MAX_RESPONSE} bytes")
        return data
