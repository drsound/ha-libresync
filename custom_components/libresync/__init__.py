"""The LibreSync integration: one client per config entry, shared by every entity.

No `DataUpdateCoordinator`. Both protocols push — volume, source, transport and
metadata all arrive as events — so the client owns the state and entities
subscribe to it. A coordinator polling every thirty seconds would give a UI that
is permanently late on a device that notifies within a second. The library keeps
a slow poll of its own for the two properties nothing announces, and that is a
safety net rather than the mechanism.
"""

import logging
from collections.abc import Callable
from typing import Any

from aiolibresync import DeviceState, LibreSyncClient, NotConnectedError
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import CONNECT_TIMEOUT, DEFAULT_NAME, DOMAIN, MANUFACTURER, PLATFORMS

_LOGGER = logging.getLogger(__name__)

# Entry data keys of minor version 2, read only by the migration.
_LEGACY_SERIAL = "serial"
_LEGACY_UDN = "udn"

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

    # Serial and model answer a moment after the ports come up, and Home
    # Assistant reads `device_info` only when an entity is added. So the device
    # is created here, and kept current from the pushed state, before and after
    # the entities register.
    assert entry.unique_id is not None  # the config flow refuses an entry without one
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.unique_id)},
        manufacturer=MANUFACTURER,
        name=DEFAULT_NAME,
    )
    update_device = _device_updater(hass, device.id)
    entry.async_on_unload(client.subscribe(update_device))
    update_device(client.state)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


def _device_updater(hass: HomeAssistant, device_id: str) -> Callable[[DeviceState], None]:
    """Keep the model and the serial on the device card current."""
    seen: tuple[str | None, str | None] | None = None

    @callback
    def update(state: DeviceState) -> None:
        nonlocal seen
        # Called on every push, including the position once a second.
        if (state.serial, state.model) == seen:
            return
        seen = (state.serial, state.model)
        changes: dict[str, Any] = {}
        if state.serial:
            changes["serial_number"] = state.serial
        if state.model:
            changes["model"] = state.model
        if changes:
            dr.async_get(hass).async_update_device(device_id, **changes)

    return update


async def async_migrate_entry(hass: HomeAssistant, entry: LibreSyncConfigEntry) -> bool:
    """Bring an entry to 1.3: keyed on the UDN, with only the host in its data.

    1.1 was keyed on the UDN already. 1.2 was keyed on the factory serial when
    the hub had one, and kept the serial and the UDN in its data: such an entry
    is re-keyed on its recorded UDN, together with its device and its entities,
    so entity ids and history survive. One with no UDN recorded cannot be
    re-keyed without asking the network, and is left for the user to add again.
    Nothing is read from the network here.
    """
    if entry.version > 1:
        return False
    if entry.minor_version < 3:
        unique_id = entry.unique_id
        serial = entry.data.get(_LEGACY_SERIAL)
        if unique_id is not None and unique_id == serial:
            udn = entry.data.get(_LEGACY_UDN)
            if not udn:
                _LOGGER.error(
                    "The hub at %s was added by its serial and its UPnP identity was "
                    "never recorded. Remove it and add it again",
                    entry.data[CONF_HOST],
                )
                return False
            await _async_rekey(hass, entry, unique_id, udn)
            unique_id = udn
        hass.config_entries.async_update_entry(
            entry,
            unique_id=unique_id,
            data={CONF_HOST: entry.data[CONF_HOST]},
            minor_version=3,
        )
    return True


async def _async_rekey(
    hass: HomeAssistant, entry: LibreSyncConfigEntry, old: str, new: str
) -> None:
    """Move the entry's device and entities from one unique ID base to another."""
    device_registry = dr.async_get(hass)
    if device := device_registry.async_get_device(identifiers={(DOMAIN, old)}):
        device_registry.async_update_device(device.id, new_identifiers={(DOMAIN, new)})

    @callback
    def migrate(entity: er.RegistryEntry) -> dict[str, Any] | None:
        if not entity.unique_id.startswith(old):
            return None
        return {"new_unique_id": new + entity.unique_id[len(old) :]}

    await er.async_migrate_entries(hass, entry.entry_id, migrate)


async def async_unload_entry(hass: HomeAssistant, entry: LibreSyncConfigEntry) -> bool:
    """Unload the platforms. The client disconnects afterwards, through the entry.

    In that order: an entity whose callback fired against a half-closed client
    would log an exception in the middle of a perfectly ordinary reload.
    """
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
