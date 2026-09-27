"""Whether sound is actually coming out — a thing only this hub knows.

`02 21` byte 8 is the only field on either port that reports it. The media
player already derives its state from it; exposing it directly lets an
automation act on it, and lets someone see where the player's state came from
when it looks wrong.
"""

from aiolibresync import LibreSyncClient, PlayState
from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import LibreSyncConfigEntry
from .entity import LibreSyncEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LibreSyncConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    assert entry.unique_id is not None
    async_add_entities([LibreSyncAudioActive(entry.runtime_data, entry.unique_id, entry.title)])


class LibreSyncAudioActive(LibreSyncEntity, BinarySensorEntity):
    _attr_translation_key = "audio"
    _attr_device_class = BinarySensorDeviceClass.SOUND
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, client: LibreSyncClient, unique_id_base: str, name: str) -> None:
        super().__init__(client, unique_id_base, name)
        self._attr_unique_id = f"{unique_id_base}_audio"

    @property
    def is_on(self) -> bool | None:
        """`PLAYING`, and nothing else.

        Paused and loading are silence too, and the byte reports both of them
        distinctly — so anything looser than an equality check here would claim
        sound was coming out during a cast handshake.
        """
        audio = self.snapshot.audio_state
        return audio is PlayState.PLAYING if audio is not None else None
