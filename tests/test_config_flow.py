"""Both ways in, and the one hub that is deliberately turned away."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import CONF_HOST
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.ssdp import SsdpServiceInfo
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.libresync.const import CONF_SERIAL, CONF_UDN, DOMAIN

from .conftest import FOUND, FOUND_WITHOUT_UDN, HOST, SERIAL, UDN


@pytest.fixture(autouse=True)
def read_serial():
    """The serial read from box 231. None by default: a unit that has none."""
    with patch(
        "custom_components.libresync.config_flow.async_read_serial",
        AsyncMock(return_value=None),
    ) as mock:
        yield mock


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
    assert result["data"] == {CONF_HOST: "192.168.10.20", CONF_UDN: UDN}


async def test_a_hub_that_does_not_answer_is_reported_not_created(hass):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    with _probe(None):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "10.0.0.1"}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_a_hub_with_neither_serial_nor_udn_is_refused_with_advice(hass):
    """`LibreDmr` serves the UDN and has been seen dead on a healthy hub.

    The device is fully controllable in that state, so this is a real case and
    not a hypothetical. With no factory serial either, it is refused: an entry
    with no `unique_id` can never be told apart from a second hub, and a mains
    cycle is measured to bring the daemon back. The error text carries that.
    """
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    with _probe(FOUND_WITHOUT_UDN):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "192.168.10.20"}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "no_identity"}


async def test_the_same_hub_cannot_be_added_twice(hass, config_entry):
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    with _probe(FOUND):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "192.168.10.20"}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


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
    assert result["data"] == {CONF_HOST: HOST, CONF_UDN: UDN}


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


async def test_a_hub_with_a_serial_is_keyed_on_it_and_keeps_both(hass, read_serial):
    """The serial is preferred: it does not depend on the UPnP daemon."""
    read_serial.return_value = SERIAL
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    with _probe(FOUND):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: HOST}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == SERIAL
    assert result["data"] == {CONF_HOST: HOST, CONF_SERIAL: SERIAL, CONF_UDN: UDN}


async def test_a_hub_whose_upnp_daemon_is_down_is_accepted_by_its_serial(hass, read_serial):
    """What the serial buys: the case the UDN alone had to refuse."""
    read_serial.return_value = SERIAL
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    with _probe(FOUND_WITHOUT_UDN):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: HOST}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == SERIAL
    assert result["data"] == {CONF_HOST: HOST, CONF_SERIAL: SERIAL}


async def test_discovery_recognises_a_serial_keyed_hub_by_its_udn_without_touching_it(
    hass, read_serial
):
    """Discovery sees only the UDN. The entry's data carries it, so no serial read is needed."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=SERIAL,
        minor_version=2,
        data={CONF_HOST: HOST, CONF_SERIAL: SERIAL, CONF_UDN: UDN},
    )
    entry.add_to_hass(hass)
    moved = SsdpServiceInfo(
        ssdp_usn=SSDP_INFO.ssdp_usn,
        ssdp_st=SSDP_INFO.ssdp_st,
        ssdp_location="http://192.168.10.99:38400/description.xml",
        upnp=SSDP_INFO.upnp,
    )
    with _probe(FOUND) as probe:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "ssdp"}, data=moved
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_HOST] == "192.168.10.99"
    probe.assert_not_awaited()
    read_serial.assert_not_awaited()


async def test_a_udn_keyed_hub_added_again_by_hand_is_recognised_before_its_serial_is_read(
    hass, read_serial, config_entry
):
    """Reading the serial would open a second session next to the running client's.

    The running client records the serial in the entry instead.
    """
    config_entry.add_to_hass(hass)
    read_serial.return_value = SERIAL
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    with _probe(FOUND):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: HOST}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert config_entry.unique_id == UDN  # never re-keyed
    read_serial.assert_not_awaited()


async def test_discovery_of_a_new_hub_with_a_serial_is_keyed_on_the_serial(hass, read_serial):
    read_serial.return_value = SERIAL
    with _probe(FOUND):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "ssdp"}, data=SSDP_INFO
        )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == SERIAL
    assert result["data"] == {CONF_HOST: HOST, CONF_SERIAL: SERIAL, CONF_UDN: UDN}


async def test_discovery_finds_by_serial_a_hub_whose_entry_lacks_the_udn(hass, read_serial):
    """Added while its UPnP daemon was down, so the entry never learned the UDN."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=SERIAL,
        minor_version=2,
        data={CONF_HOST: HOST, CONF_SERIAL: SERIAL},
    )
    entry.add_to_hass(hass)
    read_serial.return_value = SERIAL
    with _probe(FOUND):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "ssdp"}, data=SSDP_INFO
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_UDN] == UDN


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


async def test_an_ignored_hub_is_dropped_before_anything_is_sent_to_it(hass, read_serial):
    """ "Ignore" records the UDN, which discovery sees before contacting the hub."""
    MockConfigEntry(domain=DOMAIN, source="ignore", unique_id=UDN).add_to_hass(hass)
    with _probe(FOUND) as probe:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "ssdp"}, data=SSDP_INFO
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    probe.assert_not_awaited()
    read_serial.assert_not_awaited()
