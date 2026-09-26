"""What a stranger sends when their hub misbehaves.

Phase 5 of this project is beta testers on hardware nobody here owns, and this
is the only thing that makes their bug reports actionable. It ships in the first
release for that reason rather than being bolted on later.

The library assembles most of it — the state, the frames it could not decode,
the declared model — with the track metadata already redacted. This adds the
config entry and hides the address.
"""

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant

from . import LibreSyncConfigEntry
from .const import CONF_SERIAL

TO_REDACT = {CONF_HOST, CONF_SERIAL}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: LibreSyncConfigEntry
) -> dict[str, Any]:
    return {
        "entry": {
            "title": entry.title,
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            # The UDN is not redacted, deliberately. It is a runtime-generated
            # UUID rather than a serial number, it identifies which unit a
            # report is about, and without it two hubs in one report cannot be
            # told apart. The factory serial is redacted, and so is the
            # unique_id when that is what it holds.
            "unique_id": (
                "**REDACTED**"
                if entry.unique_id and entry.unique_id == entry.data.get(CONF_SERIAL)
                else entry.unique_id
            ),
        },
        "client": entry.runtime_data.diagnostics(),
    }
