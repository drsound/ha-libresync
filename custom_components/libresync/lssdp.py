"""Libre's own discovery, asked of one host: which Libre module the hub runs.

A copy of `aiolibresync.async_query_lssdp` (0.5.3), reduced to the module.
Home Assistant 2026.11 depends on aiolibresync 0.5.1, which does not have it,
and hassfest refuses a custom integration that requires a newer version than
core's. Use the library's once core moves past 0.5.1.
"""

import asyncio
import contextlib
import logging
import socket
from dataclasses import dataclass
from typing import Any, Final

_LOGGER = logging.getLogger(__name__)

#: Both hub generations answer this query on UDP 1800 with a `200 OK` that names
#: the module (`SOURCE_LIST:LS9::…` or `LS10::…`). The Stereo Hub here answered
#: 5 of 5 unicast queries in 6 to 12 ms on 2026-11-08.
LSSDP_PORT: Final = 1800
LSSDP_QUERY: Final = (
    b"M-SEARCH * HTTP/1.1\r\nHOST:239.255.255.250:1800\r\nPROTOCOL:Version 1.0\r\n\r\n"
)
LSSDP_TIMEOUT: Final = 3.0
LSSDP_RETRY_INTERVAL: Final = 0.5


@dataclass(frozen=True, slots=True)
class LssdpInfo:
    """What the hub said on LSSDP: `LS9` on the Stereo Hub, `LS10` on the newer ones."""

    module: str | None = None


def parse_lssdp(datagram: bytes) -> LssdpInfo | None:
    """An LSSDP `200 OK`, or None if `datagram` is not one."""
    if not datagram.startswith(b"HTTP/1.1 200"):
        return None
    headers: dict[str, str] = {}
    for line in datagram.decode("utf-8", errors="replace").splitlines()[1:]:
        name, separator, value = line.partition(":")
        if separator:
            headers[name.strip().lower()] = value.strip()
    if "source_list" not in headers and "fwversion" not in headers:
        return None
    return LssdpInfo(module=headers.get("source_list", "").partition("::")[0] or None)


async def async_query_lssdp(
    host: str,
    *,
    timeout: float = LSSDP_TIMEOUT,  # noqa: ASYNC109 — a readiness wait, as in the library
) -> LssdpInfo | None:
    """Ask `host` alone on Libre's discovery port.

    None means unknown, not "the original hub": no answer within `timeout`, no
    IPv4 address, or no socket. The query is one UDP datagram, sent again every
    `LSSDP_RETRY_INTERVAL`, and nothing in it changes the hub. The `timeout`
    covers name resolution and the socket too, as in aiolibresync 0.5.3.
    """
    try:
        async with asyncio.timeout(timeout):
            return await _ask_lssdp(host)
    except TimeoutError:
        _LOGGER.debug("%s did not answer on LSSDP", host)
        return None


async def _ask_lssdp(host: str) -> LssdpInfo | None:
    """Ask `host` on LSSDP until it answers; the caller bounds the wait."""
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(
            host, LSSDP_PORT, family=socket.AF_INET, type=socket.SOCK_DGRAM
        )
    except OSError:
        return None
    address = str(infos[0][4][0])
    addresses = {str(info[4][0]) for info in infos}
    answer: asyncio.Future[LssdpInfo] = loop.create_future()

    class _Listener(asyncio.DatagramProtocol):
        def datagram_received(self, data: bytes, addr: tuple[str | Any, int]) -> None:
            if str(addr[0]) not in addresses or answer.done():
                return
            if (info := parse_lssdp(data)) is not None:
                answer.set_result(info)

    try:
        transport, _protocol = await loop.create_datagram_endpoint(
            _Listener, local_addr=("0.0.0.0", 0), family=socket.AF_INET
        )
    except OSError as err:
        _LOGGER.debug("no socket for the LSSDP query: %s", err)
        return None
    try:
        while True:
            transport.sendto(LSSDP_QUERY, (address, LSSDP_PORT))
            with contextlib.suppress(TimeoutError):
                return await asyncio.wait_for(asyncio.shield(answer), LSSDP_RETRY_INTERVAL)
    finally:
        transport.close()
