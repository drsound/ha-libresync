"""Three switches out of one class. Power is here and not on the media player."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from aiolibresync import DeviceState, LibreSyncClient
from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import LibreSyncConfigEntry
from .entity import LibreSyncEntity, handle_errors


@dataclass(frozen=True, kw_only=True)
class LibreSyncSwitchDescription(SwitchEntityDescription):
    """Which field to read and which method to call. Nothing else differs."""

    value: Callable[[DeviceState], bool | None]
    set_value: Callable[[LibreSyncClient, bool], Awaitable[None]]


SWITCHES: tuple[LibreSyncSwitchDescription, ...] = (
    LibreSyncSwitchDescription(
        key="power",
        translation_key="power",
        icon="mdi:power",
        value=lambda state: state.power,
        # The client reads the current state before acting, because `02 0f` is a
        # toggle that ignores its data byte. The vendor's own app skips that
        # read and will switch a powered-on hub off from a stale cache; that is
        # the bug this integration exists not to reproduce.
        set_value=lambda client, on: client.async_set_power(on),
    ),
    LibreSyncSwitchDescription(
        key="room_correction",
        translation_key="room_correction",
        entity_category=EntityCategory.CONFIG,
        value=lambda state: state.room_correction,
        set_value=lambda client, on: client.async_set_room_correction(on),
    ),
    LibreSyncSwitchDescription(
        key="manual_eq",
        translation_key="manual_eq",
        entity_category=EntityCategory.CONFIG,
        value=lambda state: state.manual_eq,
        # Independent of room correction, not exclusive with it: both can be on
        # at once, which is why these are two switches and not one selector.
        set_value=lambda client, on: client.async_set_manual_eq(on),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LibreSyncConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    assert entry.unique_id is not None
    async_add_entities(
        LibreSyncSwitch(entry.runtime_data, entry.unique_id, description)
        for description in SWITCHES
    )


class LibreSyncSwitch(LibreSyncEntity, SwitchEntity):
    entity_description: LibreSyncSwitchDescription

    def __init__(
        self,
        client: LibreSyncClient,
        unique_id_base: str,
        description: LibreSyncSwitchDescription,
    ) -> None:
        super().__init__(client, unique_id_base)
        self.entity_description = description
        self._attr_unique_id = f"{unique_id_base}_{description.key}"

    @property
    def is_on(self) -> bool | None:
        """`None` means "not reported yet", and Home Assistant renders it unknown.

        Room correction is announced by nobody — not by the app, not by the
        remote, not by us — so the library's poll loop is what fills it in, and
        until that has run once the honest answer is that we do not know.
        Rendering it as off would invent a fact.
        """
        return self.entity_description.value(self.snapshot)

    @handle_errors
    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.entity_description.set_value(self._client, True)

    @handle_errors
    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.entity_description.set_value(self._client, False)
