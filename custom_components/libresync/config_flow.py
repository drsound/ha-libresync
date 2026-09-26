"""Two ways in, one identity: the factory serial, else the UPnP UDN.

The serial comes from message box 231 and does not depend on the UPnP daemon
`LibreDmr`, which has been seen dead on an otherwise healthy hub. The UDN is
what discovery sees without connecting, and the fallback for a unit that has no
valid factory serial. The `unique_id` is the serial when there is one and the
UDN otherwise, chosen once when the entry is created. Both identifiers are
stored in the entry's data, so a hub is recognised whichever one a later path
sees. A hub with neither is refused, because nothing could tell it apart from a
second one.

The manual path is not a fallback for awkward networks. It works when
`LibreDmr` is down, so discovery is never a precondition for adding a device.
"""

from typing import Any
from urllib.parse import urlparse

import voluptuous as vol
from aiolibresync import DiscoveredDevice, async_probe, async_probe_control, async_read_serial
from homeassistant.config_entries import (
    DISCOVERY_SOURCES,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
)
from homeassistant.const import CONF_HOST
from homeassistant.data_entry_flow import AbortFlow
from homeassistant.helpers.selector import TextSelector
from homeassistant.helpers.service_info.ssdp import (
    ATTR_UPNP_FRIENDLY_NAME,
    ATTR_UPNP_UDN,
    SsdpServiceInfo,
)

from .const import CONF_SERIAL, CONF_UDN, DEFAULT_NAME, DOMAIN

STEP_USER_SCHEMA = vol.Schema({vol.Required(CONF_HOST): TextSelector()})


class LibreSyncConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add a hub by address, or accept one Home Assistant found."""

    VERSION = 1
    #: 2 records the identifiers in the entry's data; see `async_migrate_entry`.
    MINOR_VERSION = 2

    def __init__(self) -> None:
        self._discovered: DiscoveredDevice | None = None
        self._serial: str | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """The address the user typed. Probed, not trusted."""
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST]
            device = await async_probe(host)
            if device is None:
                errors["base"] = "cannot_connect"
            else:
                # Before the serial is read, which opens a second session next to
                # the running client's.
                self._async_abort_if_known(host, udn=device.udn)
                serial = await async_read_serial(host)
                if serial is None and device.udn is None:
                    # No factory serial and the UPnP daemon down: reachable and
                    # controllable, but nothing distinguishes it from a second
                    # hub. What makes refusing acceptable is the message, which
                    # says how to bring the daemon back.
                    errors["base"] = "no_identity"
                else:
                    return await self._async_create(
                        device.name or DEFAULT_NAME, host, serial, device.udn
                    )
        return self.async_show_form(step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors)

    async def async_step_ssdp(self, discovery_info: SsdpServiceInfo) -> ConfigFlowResult:
        """A MediaRenderer answered a search. Confirm it is one of ours.

        The UDN arrives in the `USN` header, so a hub already added is
        recognised before anything is sent to it. The address comes from
        `LOCATION`, never from the description's `URLBase`, which on this
        hardware still carries an address from months ago.
        """
        udn = discovery_info.upnp.get(ATTR_UPNP_UDN)
        location = discovery_info.ssdp_location
        if not udn or not location:
            return self.async_abort(reason="cannot_connect")
        host = urlparse(location).hostname
        if not host:
            return self.async_abort(reason="cannot_connect")

        # A hub that moved is followed silently rather than reported as new, and
        # it is recognised here without touching it: the running client already
        # holds a session to it.
        self._async_abort_if_known(host, udn=udn)
        # The UDN is the unique ID until the entry is created. "Ignore" records
        # it, so an ignored hub is dropped here on its next discovery without
        # being contacted, and so is a second discovery of one being set up.
        await self.async_set_unique_id(udn)
        self._abort_if_unique_id_configured()

        # Generous matcher, strict probe. The description says what a host
        # claims to be; the connection to 50006, which sends nothing, says what
        # it is. Only then is the serial read.
        if not await async_probe_control(host):
            return self.async_abort(reason="cannot_connect")
        self._serial = await async_read_serial(host)
        self._async_abort_if_known(host, serial=self._serial, udn=udn)

        # The name comes from the description Home Assistant has already fetched.
        self._discovered = DiscoveredDevice(
            host=host, udn=udn, name=discovery_info.upnp.get(ATTR_UPNP_FRIENDLY_NAME)
        )
        self.context["title_placeholders"] = {"name": self._discovered.name or DEFAULT_NAME}
        return await self.async_step_discovery_confirm()

    async def async_step_discovery_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask before adding. A hub is a piece of the user's hi-fi, not a sensor."""
        assert self._discovered is not None
        if user_input is not None:
            # Through the same checks as a hub typed by hand, which may have been
            # added in the meantime; the serial becomes the unique ID here.
            return await self._async_create(
                self._discovered.name or DEFAULT_NAME,
                self._discovered.host,
                self._serial,
                self._discovered.udn,
            )
        self._set_confirm_only()
        return self.async_show_form(
            step_id="discovery_confirm",
            description_placeholders={"name": self._discovered.name or DEFAULT_NAME},
        )

    async def _async_create(
        self, title: str, host: str, serial: str | None, udn: str | None
    ) -> ConfigFlowResult:
        self._async_abort_if_known(host, serial=serial, udn=udn)
        await self.async_set_unique_id(serial or udn, raise_on_progress=False)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title=title, data=_entry_data(host, serial, udn))

    def _async_abort_if_known(
        self, host: str, *, serial: str | None = None, udn: str | None = None
    ) -> None:
        """Abort if any entry already carries either identifier, following its address.

        The `unique_id` alone is not enough: an entry keyed on the UDN must still
        be found by a path that only sees the serial, and the other way round.
        Identifiers the entry did not have yet are filled in on the way. The
        entry is reloaded as `_abort_if_unique_id_configured` would: when its
        data changed, or when a discovery finds a hub whose setup is waiting to
        retry.
        """
        seen = {value for value in (serial, udn) if value}
        for entry in self._async_current_entries(include_ignore=False):
            known = {entry.unique_id, entry.data.get(CONF_SERIAL), entry.data.get(CONF_UDN)}
            if seen & known:
                updates: dict[str, Any] = {CONF_HOST: host}
                if serial and not entry.data.get(CONF_SERIAL):
                    updates[CONF_SERIAL] = serial
                if udn and not entry.data.get(CONF_UDN):
                    updates[CONF_UDN] = udn
                changed = self.hass.config_entries.async_update_entry(
                    entry, data={**entry.data, **updates}
                )
                retrying = entry.state is ConfigEntryState.SETUP_RETRY
                if (changed and (retrying or entry.state is ConfigEntryState.LOADED)) or (
                    retrying and self.source in DISCOVERY_SOURCES
                ):
                    self.hass.config_entries.async_schedule_reload(entry.entry_id)
                raise AbortFlow("already_configured")


def _entry_data(host: str, serial: str | None, udn: str | None) -> dict[str, Any]:
    data: dict[str, Any] = {CONF_HOST: host}
    if serial:
        data[CONF_SERIAL] = serial
    if udn:
        data[CONF_UDN] = udn
    return data
