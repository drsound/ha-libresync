"""The LibreSync integration: one client per config entry, shared by every entity.

No `DataUpdateCoordinator`. Both protocols push — volume, source, transport and
metadata all arrive as events — so the client owns the state and entities
subscribe to it. A coordinator polling every thirty seconds would give a UI that
is permanently late on a device that notifies within a second. The library keeps
a slow poll of its own for the two properties nothing announces, and that is a
safety net rather than the mechanism.
"""

import asyncio
import logging
from collections.abc import Callable

from aiolibresync import (
    DeviceState,
    LibreSyncClient,
    LssdpInfo,
    NotConnectedError,
    async_query_lssdp,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr

from .const import CONNECT_TIMEOUT, DOMAIN, MANUFACTURER, PLATFORMS, UNSUPPORTED_MODULES

_LOGGER = logging.getLogger(__name__)

type LibreSyncConfigEntry = ConfigEntry[LibreSyncClient]


def unsupported_module(info: LssdpInfo | None) -> str | None:
    """The module LSSDP named, if this version cannot drive it.

    A hub that did not answer on LSSDP is not refused: None means unknown, and
    the original Stereo Hub answers every time.
    """
    if info is not None and info.module in UNSUPPORTED_MODULES:
        return info.module
    return None


async def async_setup_entry(hass: HomeAssistant, entry: LibreSyncConfigEntry) -> bool:
    """Connect to the hub, wait until both ports answer, and hand over the client.

    A hub that does not answer in time is `ConfigEntryNotReady`: Home Assistant
    retries in the background, and nothing else waits on it. The library
    disconnects again before raising, so a retry leaves no client behind. Once
    set up, a later disconnection is not a setup failure: the entities go
    unavailable and the client reconnects on its own.
    """
    host = entry.data[CONF_HOST]
    client = LibreSyncClient(host)
    # The module is asked alongside the connection, so a hub that is down waits
    # no longer. An LS10 hub, which this version cannot drive, fails setup with
    # that reason instead of retrying for ever, even when it was added by an
    # older version or through discovery before this check existed.
    try:
        connected, info = await asyncio.gather(
            client.async_connect(timeout=CONNECT_TIMEOUT),
            async_query_lssdp(host),
            return_exceptions=True,
        )
    except asyncio.CancelledError:
        # The connection may have opened while the query was still pending.
        await client.async_disconnect()
        raise
    if isinstance(info, BaseException):
        await client.async_disconnect()
        raise info
    if (module := unsupported_module(info)) is not None:
        await client.async_disconnect()
        raise ConfigEntryError(
            translation_domain=DOMAIN,
            translation_key="unsupported_hub",
            translation_placeholders={"host": host, "module": module},
        )
    if isinstance(connected, NotConnectedError):
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="cannot_connect",
            translation_placeholders={"host": host},
        ) from connected
    if isinstance(connected, BaseException):
        raise connected

    # Through the entry rather than in `async_unload_entry`: Home Assistant runs
    # these after the platforms have unloaded.
    entry.async_on_unload(client.async_disconnect)
    entry.runtime_data = client

    # The model answers a moment after the ports come up, and Home Assistant
    # reads `device_info` only when an entity is added. So the device is created
    # here, and kept current from the pushed state, before and after the
    # entities register. The hub's serial is a production code of the Libre
    # module, not the serial printed on the product, so the card does not carry
    # it; versions up to 0.2.1 put it there, and this clears it.
    assert entry.unique_id is not None  # the config flow refuses an entry without one
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.unique_id)},
        manufacturer=MANUFACTURER,
        name=entry.title,
    )
    if device.serial_number is not None:
        dr.async_get(hass).async_update_device(device.id, serial_number=None)
    update_device = _device_updater(hass, device.id)
    entry.async_on_unload(client.subscribe(update_device))
    update_device(client.state)
    log_availability = _availability_logger(entry.data[CONF_HOST], client.state.available)
    entry.async_on_unload(client.subscribe(log_availability))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


def _device_updater(hass: HomeAssistant, device_id: str) -> Callable[[DeviceState], None]:
    """Keep the model on the device card current."""
    seen: str | None = None

    @callback
    def update(state: DeviceState) -> None:
        nonlocal seen
        # Called on every push, including the position once a second.
        if not state.model or state.model == seen:
            return
        seen = state.model
        dr.async_get(hass).async_update_device(device_id, model=state.model)

    return update


def _availability_logger(host: str, available: bool) -> Callable[[DeviceState], None]:
    """Log once when the hub becomes unavailable and once when it is back.

    Here rather than in the entities, which would each log the same drop. The
    client reconnects for ever without logging above debug, so this is the only
    trace of an outage at the default level.
    """

    @callback
    def log(state: DeviceState) -> None:
        nonlocal available
        if state.available == available:
            return
        available = state.available
        if available:
            _LOGGER.info("The hub at %s is back online", host)
        else:
            _LOGGER.info("The hub at %s is unavailable", host)

    return log


async def async_unload_entry(hass: HomeAssistant, entry: LibreSyncConfigEntry) -> bool:
    """Unload the platforms. The client disconnects afterwards, through the entry.

    In that order: an entity whose callback fired against a half-closed client
    would log an exception in the middle of a perfectly ordinary reload.
    """
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
