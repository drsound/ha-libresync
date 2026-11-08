"""The bundled LSSDP query, against a fake hub on localhost.

The two tests that exchange datagrams enable sockets, which the harness blocks;
they only ever talk to 127.0.0.1.
"""

import asyncio
import contextlib
import socket

import pytest

from custom_components.libresync import lssdp

#: The reply this hub sent on 2026-10-23, identifiers replaced.
LSSDP_REPLY = (
    b"HTTP/1.1 200 OK\r\nUSN:0123456789ab\r\nHOST: 239.255.255.250:1800\r\n"
    b"Version:LSSDP 1.0\r\nFN:1\r\nFWVERSION:p15262.515.0\r\n"
    b"CAST_FWVERSION:1.52.15262\r\nCAST_TIMEZONE:\r\nCAST_MODEL:Stereo Hub\r\n"
    b"PORT:7777\r\nDeviceName:Stereo\r\nState:F\r\nNETMODE:WLAN\r\n"
    b"SPEAKERTYPE:0\r\nTCPPORT:2020\r\nWIFIBAND:2G\r\n"
    b"SOURCE_LIST:LS9::f7ffffff\r\nMRAMode:APMS\r\n\r\n"
)


def test_the_reply_names_the_module():
    assert lssdp.parse_lssdp(LSSDP_REPLY) == lssdp.LssdpInfo(module="LS9")
    newer = LSSDP_REPLY.replace(b"LS9::f7ffffff", b"LS10::01000010")
    assert lssdp.parse_lssdp(newer) == lssdp.LssdpInfo(module="LS10")


def test_anything_else_is_not_a_reply():
    assert lssdp.parse_lssdp(b"M-SEARCH * HTTP/1.1\r\nHOST:x\r\n\r\n") is None
    assert lssdp.parse_lssdp(b"HTTP/1.1 200 OK\r\nLOCATION: http://x/\r\n\r\n") is None


class _Responder(asyncio.DatagramProtocol):
    def __init__(self, ignore: int) -> None:
        self.ignore = ignore
        self.queries = 0
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport) -> None:
        self.transport = transport

    def datagram_received(self, data: bytes, addr) -> None:
        if not data.startswith(b"M-SEARCH") or self.transport is None:
            return
        self.queries += 1
        if self.queries > self.ignore:
            self.transport.sendto(LSSDP_REPLY, addr)


@contextlib.asynccontextmanager
async def _responder(monkeypatch, ignore: int = 0):
    transport, responder = await asyncio.get_running_loop().create_datagram_endpoint(
        lambda: _Responder(ignore), local_addr=("127.0.0.1", 0)
    )
    monkeypatch.setattr(lssdp, "LSSDP_PORT", transport.get_extra_info("sockname")[1])
    try:
        yield responder
    finally:
        transport.close()


@pytest.mark.usefixtures("socket_enabled")
async def test_the_query_is_sent_again_until_the_hub_answers(monkeypatch):
    monkeypatch.setattr(lssdp, "LSSDP_RETRY_INTERVAL", 0.05)
    async with _responder(monkeypatch, ignore=2) as responder:
        info = await lssdp.async_query_lssdp("127.0.0.1", timeout=3.0)
    assert info == lssdp.LssdpInfo(module="LS9")
    assert responder.queries == 3


@pytest.mark.usefixtures("socket_enabled")
async def test_no_answer_is_none(monkeypatch):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(("127.0.0.1", 0))
        free_port = probe.getsockname()[1]
    monkeypatch.setattr(lssdp, "LSSDP_PORT", free_port)
    monkeypatch.setattr(lssdp, "LSSDP_RETRY_INTERVAL", 0.05)
    assert await lssdp.async_query_lssdp("127.0.0.1", timeout=0.2) is None


async def test_a_name_that_does_not_resolve_is_none(monkeypatch):
    async def fail(*_args, **_kwargs):
        raise OSError("no such host")

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", fail)
    assert await lssdp.async_query_lssdp("nowhere.invalid") is None


async def test_no_socket_is_none(monkeypatch):
    async def refuse(*_args, **_kwargs):
        raise OSError("no socket")

    monkeypatch.setattr(asyncio.get_running_loop(), "create_datagram_endpoint", refuse)
    assert await lssdp.async_query_lssdp("127.0.0.1") is None
