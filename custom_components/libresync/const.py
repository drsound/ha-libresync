"""Constants. Nothing here knows about the wire — that is the library's job."""

from typing import Final

from homeassistant.const import Platform

DOMAIN: Final = "libresync"

#: The two identifiers a hub can have, both kept in the entry's data whenever
#: they are known. The `unique_id` is whichever was preferred when the entry was
#: created, and never changes; recognising a hub checks all three.
CONF_SERIAL: Final = "serial"
CONF_UDN: Final = "udn"

PLATFORMS: Final = [
    Platform.MEDIA_PLAYER,
    Platform.SWITCH,
    Platform.SELECT,
    Platform.BINARY_SENSOR,
]

#: What the hardware is, for `device_info`. The device description says
#: `LibreWireless` in one word; this is the reading form of the same thing.
MANUFACTURER: Final = "Libre Wireless"

#: The name a hub gets when it will not say what it is called. Every path
#: through the config flow can reach it, so it is one string rather than four.
DEFAULT_NAME: Final = "Stereo Hub"

#: How long setup waits for both ports before handing the retry to Home
#: Assistant. On the LAN they are up in about 0.05 s; this is for a hub that is
#: still booting or a network that is having a bad moment.
CONNECT_TIMEOUT: Final = 10.0
