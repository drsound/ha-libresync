"""Constants. Nothing here knows about the wire — that is the library's job."""

from typing import Final

from homeassistant.const import Platform

DOMAIN: Final = "libresync"

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
DEFAULT_NAME: Final = "LibreSync hub"

#: How long setup waits for both ports before handing the retry to Home
#: Assistant. On the LAN they are up in about 0.05 s; this is for a hub that is
#: still booting or a network that is having a bad moment.
CONNECT_TIMEOUT: Final = 10.0

#: The Libre module the hub reported over LSSDP when it was added: `LS9` on the
#: original Stereo Hub, `LS10` on the newer ones. Absent on entries added before
#: 0.3, which are all hubs that worked with both ports.
CONF_MODULE: Final = "module"

#: Modules whose port 7777 is wrapped in mutual TLS. Without a client
#: certificate, such a hub runs on port 50006 alone: power and source.
TLS_MODULES: Final = frozenset({"LS10"})

#: Power is not pushed on those hubs when it changes on the remote, so it is
#: polled more often than the library's default.
TLS_POLL_INTERVAL: Final = 10.0
