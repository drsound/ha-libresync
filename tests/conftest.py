"""Fixtures shared by every test: a client that never opens a socket."""

from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock, create_autospec, patch

import pytest
from aiolibresync import (
    DeviceState,
    DiscoveredDevice,
    LibreSyncClient,
    LssdpInfo,
    PlayState,
    Source,
)
from homeassistant.const import CONF_HOST
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.libresync.const import DOMAIN

#: Fabricated, and shaped like the real thing: a v1 UUID, because that is what
#: `LibreDmr` mints and what the config flow keys entries on. Not a real hub's —
#: a UDN identifies one specific unit, and a public repository is not the place
#: for one.
UDN = "uuid:1a2b3c4d-5e6f-11f0-9193-0123456789ab"
HOST = "192.168.10.20"
#: Fabricated, in the measured shape: 20 alphanumeric characters.
SERIAL = "FAKE0SERIAL000000000"

#: A hub a second after connecting: on, on Streaming, paused. Volume 28 rather
#: than a round number, so a percentage conversion that silently rounds shows up.
FULL_STATE = DeviceState(
    available=True,
    power=True,
    volume=28,
    muted=False,
    source=Source(index=0, name="Streaming"),
    # The device's own order, which is not sorted by `ix` — Streaming is index 0
    # and comes last on the real hub. Keeping that here is what makes the test
    # for "send the ix, not the position" meaningful.
    sources=(
        Source(index=1, name="USB"),
        Source(index=6, name="HDMI"),
        Source(index=0, name="Streaming"),
    ),
    play_state=PlayState.PAUSED,
    audio_state=PlayState.PAUSED,
    room_correction=True,
    manual_eq=False,
    eq_preset=3,
    model="Stereo Hub",
)

FOUND = DiscoveredDevice(
    host=HOST,
    udn=UDN,
    name="Stereo",
    model="LibreWireless",
    manufacturer="LibreWireless",
    description_url=f"http://{HOST}:38400/description.xml",
)

#: Reachable, controllable, and unidentifiable: what `async_probe` returns while
#: `LibreDmr` is not running. Observed on real hardware, not invented.
FOUND_WITHOUT_UDN = DiscoveredDevice(host=HOST, udn=None)


#: What the original Stereo Hub answers on LSSDP: the reply this hub sent on
#: 2026-10-23, reduced to the fields the integration reads.
LS9 = LssdpInfo(module="LS9", model="Stereo Hub", firmware="p15262.515.0", media_port=7777)


@pytest.fixture(autouse=True)
def mock_lssdp() -> Iterator[AsyncMock]:
    """LSSDP, answered as the original Stereo Hub answers it.

    Patched where setup and the config flow look it up. A test that wants an
    LS10 hub, or no answer, changes the return value.
    """
    lssdp = AsyncMock(return_value=LS9)
    with (
        patch("custom_components.libresync.async_query_lssdp", lssdp),
        patch("custom_components.libresync.config_flow.async_query_lssdp", lssdp),
    ):
        yield lssdp


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Loading a custom integration is opt-in, and every test here does it."""
    yield


@pytest.fixture
def mock_client() -> MagicMock:
    """A `LibreSyncClient` that never touches a socket.

    `subscribe` records the callback and returns an unsubscribe, exactly as the
    real one does, so `push()` below can publish a new state the way the device
    would.
    """
    client = create_autospec(LibreSyncClient, instance=True)
    client.state = FULL_STATE
    client.subscribers = []

    def subscribe(callback):
        client.subscribers.append(callback)
        return lambda: client.subscribers.remove(callback)

    client.subscribe = subscribe
    client.diagnostics.return_value = {"state": {}, "unknown_frames": {}}
    return client


def push(client: MagicMock, state: DeviceState) -> None:
    """Publish a new state to every subscriber, as the client does."""
    client.state = state
    for callback in list(client.subscribers):
        callback(state)


@pytest.fixture
def config_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=UDN,
        title="Stereo Hub",
        data={CONF_HOST: HOST},
    )
