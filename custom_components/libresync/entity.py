"""What every LibreSync entity shares: a subscription, a device, and availability."""

from collections.abc import Awaitable, Callable, Coroutine
from functools import wraps
from typing import Any

from aiolibresync import ConfirmationTimeout, DeviceState, LibreSyncClient, NotConnectedError
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, MANUFACTURER


def handle_errors[**P](
    func: Callable[P, Awaitable[None]],
) -> Callable[P, Coroutine[Any, Any, None]]:
    """Turn the library's failures into errors Home Assistant shows the user.

    Every command goes through here, so a hub that is not connected or does not
    confirm reads as a sentence in the interface rather than a traceback.
    """

    @wraps(func)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> None:
        try:
            await func(*args, **kwargs)
        except NotConnectedError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="not_connected"
            ) from err
        except ConfirmationTimeout as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="not_confirmed"
            ) from err

    return wrapper


class LibreSyncEntity(Entity):
    """Subscribes to the client and writes itself on every state change.

    No polling: `should_poll` is False and the client pushes. The subscription
    is taken in `async_added_to_hass` rather than in `__init__`, so an entity
    that is built and never added leaves no callback behind on the client.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, client: LibreSyncClient, unique_id_base: str, name: str) -> None:
        self._client = client
        # No model here: setup keeps it on the device as it arrives, and a
        # None here, before it has, would overwrite it.
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, unique_id_base)},
            manufacturer=MANUFACTURER,
            name=name,
        )

    @property
    def snapshot(self) -> DeviceState:
        """The client's current state. Read this; never cache it.

        `DeviceState` is immutable and the client replaces it wholesale, so this
        always reads the newest one and no entity has to hold its own copy that
        could go stale.
        """
        return self._client.state

    @property
    def available(self) -> bool:
        """The TCP connection, and nothing else.

        `power` is the hub's own state and a different thing entirely: the hub
        answers on both ports while switched off. An entity that returned
        `power` here would vanish from the interface every time someone pressed
        the power button and reappear when they pressed it again.

        Conflating the two is the single most likely bug in this project, so it
        is decided here once instead of in five entity classes.
        """
        return self.snapshot.available

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._client.subscribe(self._handle_state))

    @callback
    def _handle_state(self, state: DeviceState) -> None:
        """The client's callback. Synchronous, and called from the event loop."""
        self.async_write_ha_state()

    @handle_errors
    async def async_update(self) -> None:
        """Only reached through `homeassistant.update_entity`, since polling is off.

        Re-reads every property rather than the poll loop's two, because someone
        asking for this has a reason to distrust what they are looking at.
        """
        await self._client.async_refresh()
