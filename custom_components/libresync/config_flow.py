"""Two ways in, one identity: the hub's UPnP UDN.

Discovery sees the UDN without connecting, and the manual path reads it from the
device description on port 38400. A hub whose UPnP service does not answer
cannot be added, and the error says how to bring it back: restarting the hub.
"""

from typing import Any
from urllib.parse import urlparse

import voluptuous as vol
from aiolibresync import DiscoveredDevice, async_probe, async_probe_control
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST
from homeassistant.helpers.selector import TextSelector
from homeassistant.helpers.service_info.ssdp import (
    ATTR_UPNP_FRIENDLY_NAME,
    ATTR_UPNP_UDN,
    SsdpServiceInfo,
)

from .const import DEFAULT_NAME, DOMAIN

STEP_USER_SCHEMA = vol.Schema({vol.Required(CONF_HOST): TextSelector()})


class LibreSyncConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add a hub by address, or accept one Home Assistant found."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovered: DiscoveredDevice | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """The address the user typed. Probed, not trusted."""
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST]
            device = await async_probe(host)
            if device is None:
                errors["base"] = "cannot_connect"
            elif not device.udn:
                # Reachable and controllable, but its UPnP service does not
                # answer, so nothing identifies it. The message says how to
                # bring the service back.
                errors["base"] = "no_identity"
            else:
                await self.async_set_unique_id(device.udn, raise_on_progress=False)
                self._abort_if_unique_id_configured(updates={CONF_HOST: host})
                return self.async_create_entry(
                    title=device.name or DEFAULT_NAME, data={CONF_HOST: host}
                )
        return self.async_show_form(step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors)

    async def async_step_ssdp(self, discovery_info: SsdpServiceInfo) -> ConfigFlowResult:
        """A MediaRenderer answered a search. Confirm it is one of ours.

        The UDN arrives in the `USN` header, so a hub already added, or ignored,
        is recognised before anything is sent to it, and followed to its new
        address. The address comes from `LOCATION`, never from the description's
        `URLBase`, which on this hardware still carries an address from months
        ago.
        """
        udn = discovery_info.upnp.get(ATTR_UPNP_UDN)
        host = urlparse(discovery_info.ssdp_location or "").hostname
        if not udn or not host:
            return self.async_abort(reason="cannot_connect")

        await self.async_set_unique_id(udn)
        self._abort_if_unique_id_configured(updates={CONF_HOST: host})

        # Generous matcher, strict probe. The description says what a host
        # claims to be; the connection to 50006, which sends nothing, says what
        # it is.
        if not await async_probe_control(host):
            return self.async_abort(reason="cannot_connect")

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
        name = self._discovered.name or DEFAULT_NAME
        if user_input is not None:
            return self.async_create_entry(title=name, data={CONF_HOST: self._discovered.host})
        self._set_confirm_only()
        return self.async_show_form(
            step_id="discovery_confirm", description_placeholders={"name": name}
        )
