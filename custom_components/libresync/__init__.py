"""The LibreSync integration: one client per config entry, shared by every entity.

No `DataUpdateCoordinator`. Both protocols push — volume, source, transport and
metadata all arrive as events — so the client owns the state and entities
subscribe to it. A coordinator polling every thirty seconds would give a UI that
is permanently late on a device that notifies within a second. The library keeps
a slow poll of its own for the two properties nothing announces, and that is a
safety net rather than the mechanism.
"""

from collections.abc import Callable

from aiolibresync import (
    POLL_INTERVAL,
    DeviceState,
    LibreSyncClient,
    NotConnectedError,
    async_query_lssdp,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr

from .const import (
    CONF_MODULE,
    CONNECT_TIMEOUT,
    DOMAIN,
    MANUFACTURER,
    PLATFORMS,
    TLS_MODULES,
    TLS_POLL_INTERVAL,
)

type LibreSyncConfigEntry = ConfigEntry[LibreSyncClient]


async def async_setup_entry(hass: HomeAssistant, entry: LibreSyncConfigEntry) -> bool:
    """Connect to the hub, wait until both ports answer, and hand over the client.

    A hub that does not answer in time is `ConfigEntryNotReady`: Home Assistant
    retries in the background, and nothing else waits on it. The library
    disconnects again before raising, so a retry leaves no client behind. Once
    set up, a later disconnection is not a setup failure: the entities go
    unavailable and the client reconnects on its own.
    """
    # A newer hub, whose 7777 needs a certificate, runs on 50006 alone: the
    # entities that need 7777 are not created, and power is polled more often.
    # An entry without the module, from before 0.3 or added while the LSSDP
    # reply was lost, asks again: on an LS10 hub the 7777 attempt below fails
    # and the retry asks once more, so a lost reply corrects itself.
    if CONF_MODULE not in entry.data:
        lssdp = await async_query_lssdp(entry.data[CONF_HOST])
        if lssdp is not None and lssdp.module is not None:
            hass.config_entries.async_update_entry(
                entry, data={**entry.data, CONF_MODULE: lssdp.module}
            )
    media = entry.data.get(CONF_MODULE) not in TLS_MODULES
    client = LibreSyncClient(
        entry.data[CONF_HOST],
        media=media,
        module=entry.data.get(CONF_MODULE),
        poll_interval=POLL_INTERVAL if media else TLS_POLL_INTERVAL,
    )
    try:
        await client.async_connect(timeout=CONNECT_TIMEOUT)
    except NotConnectedError as err:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="cannot_connect",
            translation_placeholders={"host": entry.data[CONF_HOST]},
        ) from err

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


async def async_unload_entry(hass: HomeAssistant, entry: LibreSyncConfigEntry) -> bool:
    """Unload the platforms. The client disconnects afterwards, through the entry.

    In that order: an entity whose callback fired against a half-closed client
    would log an exception in the middle of a perfectly ordinary reload.
    """
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
