"""The EQ preset. Choosing among presets, never defining one.

The 17-byte per-preset filter block is documented and deliberately undecoded:
users build their presets in the vendor's app, which is the one thing it does
that this cannot, and the integration selects among them.

The hub has a fourth preset nobody built: 0, the app's editor slot. The app
selects it when its editor opens, it holds the last curve edited, and the hub
reports it until another preset is chosen. Shown as its own option only while it
is the current one, so the select tells the truth without offering editing it
cannot do.
"""

from aiolibresync import LibreSyncClient
from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import LibreSyncConfigEntry
from .entity import LibreSyncEntity, handle_errors

#: The hub has exactly three, and the library refuses anything outside 1..3.
PRESETS = ["1", "2", "3"]

#: Preset 0, named for what it means rather than for its number.
EDITOR = "editor"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LibreSyncConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    assert entry.unique_id is not None
    async_add_entities([LibreSyncEqPreset(entry.runtime_data, entry.unique_id)])


class LibreSyncEqPreset(LibreSyncEntity, SelectEntity):
    _attr_translation_key = "eq_preset"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, client: LibreSyncClient, unique_id_base: str) -> None:
        super().__init__(client, unique_id_base)
        self._attr_unique_id = f"{unique_id_base}_eq_preset"

    @property
    def options(self) -> list[str]:
        # Offered only while it is true. The options are a capability attribute
        # and live in the registry, so they should not churn; this changes when
        # someone opens the app's editor, which is rare.
        if self.snapshot.eq_preset == 0:
            return [*PRESETS, EDITOR]
        return PRESETS

    @property
    def current_option(self) -> str | None:
        preset = self.snapshot.eq_preset
        if preset == 0:
            return EDITOR
        if preset is not None and str(preset) in PRESETS:
            return str(preset)
        return None

    @handle_errors
    async def async_select_option(self, option: str) -> None:
        if option == EDITOR:
            # Home Assistant only accepts an option that is listed, and this one
            # is listed only while it is already current, so there is nothing to
            # do. Nothing is sent: `08 02` with preset 0 is what the app sends,
            # and it has never been sent from here.
            return
        await self._client.async_select_eq_preset(int(option))
