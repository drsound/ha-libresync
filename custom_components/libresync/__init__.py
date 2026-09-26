"""The LibreSync integration: one client per config entry, shared by every entity.

No `DataUpdateCoordinator`. Both protocols push — volume, source, transport and
metadata all arrive as events — so the client owns the state and entities
subscribe to it. A coordinator polling every thirty seconds would give a UI that
is permanently late on a device that notifies within a second. The library keeps
a slow poll of its own for the two properties nothing announces, and that is a
safety net rather than the mechanism.
"""

import logging
from typing import Any

from aiolibresync import DeviceState, LibreSyncClient, NotConnectedError
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr

from .const import CONF_SERIAL, CONF_UDN, CONNECT_TIMEOUT, DOMAIN, PLATFORMS

_LOGGER = logging.getLogger(__name__)

type LibreSyncConfigEntry = ConfigEntry[LibreSyncClient]


async def async_setup_entry(hass: HomeAssistant, entry: LibreSyncConfigEntry) -> bool:
    """Connect to the hub, wait until both ports answer, and hand over the client.

    A hub that does not answer in time is `ConfigEntryNotReady`: Home Assistant
    retries in the background, and nothing else waits on it. The library
    disconnects again before raising, so a retry leaves no client behind. Once
    set up, a later disconnection is not a setup failure: the entities go
    unavailable and the client reconnects on its own.
    """
    client = LibreSyncClient(entry.data[CONF_HOST])
    try:
        await client.async_connect(timeout=CONNECT_TIMEOUT)
    except NotConnectedError as err:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="cannot_connect",
            translation_placeholders={"host": entry.data[CONF_HOST]},
        ) from err

    # Through the entry rather than in `async_unload_entry`: Home Assistant runs
    # these after the platforms have unloaded, and also when a later step of
    # this setup fails, which does not unload the entry.
    entry.async_on_unload(client.async_disconnect)
    entry.runtime_data = client

    seen: tuple[str | None, str | None] | None = None

    @callback
    def record_identity(state: DeviceState) -> None:
        """Keep the factory serial in the entry, and the device card current.

        The `unique_id` does not change. The serial is recorded so that a later
        path which sees only the serial still recognises this hub, including an
        entry created by UDN before the serial was read at all.

        Serial and model both answer a moment after the ports come up, and Home
        Assistant reads `device_info` only when an entity is added, so the
        device registry is updated here when either arrives.
        """
        nonlocal seen
        # Called on every push, including the position once a second.
        if (state.serial, state.model) == seen:
            return
        known = entry.data.get(CONF_SERIAL)
        if state.serial and known and state.serial != known:
            # Another hub answers at this address, which DHCP can do. Adopting
            # its serial would point discovery of either hub at this entry.
            _LOGGER.warning(
                "The hub at %s reports a different serial from the one it was "
                "added with, so its identity is not recorded",
                entry.data[CONF_HOST],
            )
            seen = (state.serial, state.model)
            return
        if state.serial and not known:
            hass.config_entries.async_update_entry(
                entry, data={**entry.data, CONF_SERIAL: state.serial}
            )
        registry = dr.async_get(hass)
        # One device per entry. Looked up by entry rather than by identifier,
        # which Home Assistant no longer guarantees unique across entries.
        devices = dr.async_entries_for_config_entry(registry, entry.entry_id)
        if not devices:
            # Before the platforms have created it. Not marked as seen, so the
            # next push tries again.
            return
        seen = (state.serial, state.model)
        changes: dict[str, Any] = {}
        if state.serial:
            changes["serial_number"] = state.serial
        if state.model:
            changes["model"] = state.model
        if changes:
            registry.async_update_device(devices[0].id, **changes)

    entry.async_on_unload(client.subscribe(record_identity))
    record_identity(client.state)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: LibreSyncConfigEntry) -> bool:
    """1.1 -> 1.2: record the identifier the entry was keyed on in its data.

    Entries from 1.1 were keyed on the UDN and stored only the host. Recording
    the UDN lets discovery recognise the hub by it even when the entry is later
    known by its serial too. Nothing is read from the network here.
    """
    if entry.version > 1:
        return False
    if entry.minor_version < 2:
        data = dict(entry.data)
        if entry.unique_id and CONF_UDN not in data and CONF_SERIAL not in data:
            data[CONF_UDN] = entry.unique_id
        hass.config_entries.async_update_entry(entry, data=data, minor_version=2)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: LibreSyncConfigEntry) -> bool:
    """Unload the platforms. The client disconnects afterwards, through the entry.

    In that order: an entity whose callback fired against a half-closed client
    would log an exception in the middle of a perfectly ordinary reload.
    """
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
