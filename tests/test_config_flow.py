"""Both ways in, and the one hub that is deliberately turned away."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.ssdp import SsdpServiceInfo
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.libresync.const import DOMAIN

from .conftest import FOUND, FOUND_WITHOUT_UDN, HOST, UDN


@pytest.fixture(autouse=True)
def no_setup():
    """The flow is under test here, not the setup a created entry triggers.

    Without this, every created entry would build a real client against an
    address nothing answers on, and wait out the connect timeout.
    """
    with patch("custom_components.libresync.async_setup_entry", return_value=True):
        yield


@contextmanager
def _probe(result) -> Iterator[AsyncMock]:
    """Both checks a hub goes through: the full probe of the manual path, and the
    control-port check of discovery, which has the description already.

    Yields the discovery one, the only one a test needs to inspect.
    """
    with (
        patch(
            "custom_components.libresync.config_flow.async_probe",
            AsyncMock(return_value=result),
        ),
        patch(
            "custom_components.libresync.config_flow.async_probe_control",
            AsyncMock(return_value=result is not None),
        ) as control,
    ):
        yield control


async def test_manual_entry_creates_an_entry_keyed_on_the_udn(hass):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] is FlowResultType.FORM

    with _probe(FOUND):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "192.168.10.20"}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == UDN
    assert result["data"] == {CONF_HOST: "192.168.10.20"}


async def test_a_hub_that_does_not_answer_is_reported_not_created(hass):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    with _probe(None):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "10.0.0.1"}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


@pytest.mark.parametrize(
    "found",
    [
        pytest.param(FOUND_WITHOUT_UDN, id="no_udn"),
        # A description whose UDN element holds only whitespace.
        pytest.param(replace(FOUND, udn=""), id="empty_udn"),
    ],
)
async def test_a_hub_without_a_udn_is_refused_with_advice(hass, found):
    """`LibreDmr` serves the UDN and has been seen dead on a healthy hub.

    The device is controllable in that state, but nothing identifies it, so it
    is refused. A mains cycle brings the daemon back, and the error says so.
    """
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    with _probe(found):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "192.168.10.20"}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "no_identity"}


async def test_the_same_hub_cannot_be_added_twice(hass, config_entry):
    """Added again at a new address, the entry follows it."""
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(config_entry, data={CONF_HOST: "192.168.10.99"})
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    with _probe(FOUND):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "192.168.10.20"}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert config_entry.data[CONF_HOST] == "192.168.10.20"


async def test_an_ignored_hub_can_still_be_added_by_hand(hass):
    MockConfigEntry(domain=DOMAIN, source="ignore", unique_id=UDN).add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    with _probe(FOUND):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "192.168.10.20"}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert [
        (entry.source, entry.unique_id) for entry in hass.config_entries.async_entries(DOMAIN)
    ] == [("user", UDN)]


SSDP_INFO = SsdpServiceInfo(
    ssdp_usn=f"{UDN}::urn:schemas-upnp-org:device:MediaRenderer:1",
    ssdp_st="urn:schemas-upnp-org:device:MediaRenderer:1",
    ssdp_location=f"http://{HOST}:38400/description.xml",
    upnp={"UDN": UDN, "manufacturer": "LibreWireless", "friendlyName": "Stereo"},
)


async def test_discovery_asks_before_it_adds(hass):
    with _probe(FOUND):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "ssdp"}, data=SSDP_INFO
        )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "discovery_confirm"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == UDN
    assert result["data"] == {CONF_HOST: HOST}


async def test_discovery_of_a_hub_already_added_follows_its_new_address(hass, config_entry):
    """The address can change; the UDN cannot. Follow the move without asking."""
    config_entry.add_to_hass(hass)
    moved = SsdpServiceInfo(
        ssdp_usn=SSDP_INFO.ssdp_usn,
        ssdp_st=SSDP_INFO.ssdp_st,
        ssdp_location="http://192.168.10.99:38400/description.xml",
        upnp=SSDP_INFO.upnp,
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "ssdp"}, data=moved
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert config_entry.data[CONF_HOST] == "192.168.10.99"


async def test_discovery_that_the_tcp_probe_disowns_is_dropped(hass):
    """A MediaRenderer that answers SSDP but not on 50006 is not one of ours.

    The description proves what a host claims to be; the probe proves what it
    is. That is exactly why the manifest's matchers are allowed to be generous.
    """
    with _probe(None):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "ssdp"}, data=SSDP_INFO
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "cannot_connect"


async def test_discovery_without_a_udn_in_the_usn_is_dropped(hass):
    """Nothing to key an entry on, and no reason to ask the user about it."""
    anonymous = SsdpServiceInfo(
        ssdp_usn="",
        ssdp_st=SSDP_INFO.ssdp_st,
        ssdp_location=SSDP_INFO.ssdp_location,
        upnp={"manufacturer": "LibreWireless"},
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "ssdp"}, data=anonymous
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "cannot_connect"


async def test_discovery_whose_location_names_no_host_is_dropped(hass):
    """A `LOCATION` that parses but carries no host leaves nothing to connect to."""
    hostless = SsdpServiceInfo(
        ssdp_usn=SSDP_INFO.ssdp_usn,
        ssdp_st=SSDP_INFO.ssdp_st,
        ssdp_location="http:///description.xml",
        upnp=SSDP_INFO.upnp,
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "ssdp"}, data=hostless
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "cannot_connect"


@pytest.mark.parametrize(
    ("state", "reloaded"),
    [(ConfigEntryState.SETUP_RETRY, True), (ConfigEntryState.LOADED, False)],
)
async def test_discovery_of_a_hub_waiting_to_retry_retries_it_now(
    hass, config_entry, state, reloaded
):
    """Found again at the same address: a setup waiting to retry is retried now."""
    config_entry.add_to_hass(hass)
    config_entry.mock_state(hass, state)
    with patch.object(hass.config_entries, "async_schedule_reload") as reload:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "ssdp"}, data=SSDP_INFO
        )
    assert result["reason"] == "already_configured"
    assert reload.called is reloaded


async def test_a_pending_discovery_goes_away_when_the_hub_is_added_by_hand(hass):
    with _probe(FOUND):
        discovered = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "ssdp"}, data=SSDP_INFO
        )
        assert discovered["type"] is FlowResultType.FORM
        manual = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        manual = await hass.config_entries.flow.async_configure(
            manual["flow_id"], {CONF_HOST: HOST}
        )
    assert manual["type"] is FlowResultType.CREATE_ENTRY
    assert hass.config_entries.flow.async_progress() == []


async def test_a_second_discovery_of_a_hub_being_set_up_is_dropped_unprobed(hass):
    with _probe(FOUND) as probe:
        first = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "ssdp"}, data=SSDP_INFO
        )
        second = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "ssdp"},
            data=replace(SSDP_INFO, ssdp_st="upnp:rootdevice"),
        )
    assert first["type"] is FlowResultType.FORM
    assert second["type"] is FlowResultType.ABORT
    assert second["reason"] == "already_in_progress"
    probe.assert_awaited_once()


async def test_an_ignored_hub_is_dropped_before_anything_is_sent_to_it(hass):
    """ "Ignore" records the UDN, which discovery sees before contacting the hub."""
    MockConfigEntry(domain=DOMAIN, source="ignore", unique_id=UDN).add_to_hass(hass)
    with _probe(FOUND) as probe:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "ssdp"}, data=SSDP_INFO
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    probe.assert_not_awaited()
