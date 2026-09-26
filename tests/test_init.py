"""Setting the entry up, and taking it down again."""

from unittest.mock import patch

from aiolibresync import NotConnectedError
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.libresync.const import CONF_SERIAL, CONF_UDN, CONNECT_TIMEOUT, DOMAIN

from .conftest import FULL_STATE, HOST, SERIAL, UDN, push


async def test_setup_connects_and_unload_disconnects(hass, config_entry, mock_client):
    config_entry.add_to_hass(hass)
    with patch("custom_components.libresync.LibreSyncClient", return_value=mock_client):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.LOADED
    assert config_entry.runtime_data is mock_client
    mock_client.async_connect.assert_awaited_once_with(timeout=CONNECT_TIMEOUT)

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()
    mock_client.async_disconnect.assert_awaited_once()


async def test_the_client_is_built_with_the_configured_host(hass, config_entry, mock_client):
    config_entry.add_to_hass(hass)
    with patch("custom_components.libresync.LibreSyncClient", return_value=mock_client) as factory:
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()
    assert factory.call_args.args[0] == "192.168.10.20"


async def test_a_hub_that_never_answers_is_retried(hass, config_entry, mock_client):
    """Bronze `test-before-setup`: an unreachable hub is `ConfigEntryNotReady`.

    The library waits for the ports and, on giving up, disconnects before it
    raises, so a retry leaves no reconnect loop behind.
    """
    mock_client.async_connect.side_effect = NotConnectedError("no answer")
    config_entry.add_to_hass(hass)
    with patch("custom_components.libresync.LibreSyncClient", return_value=mock_client):
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.SETUP_RETRY
    assert "192.168.10.20" in config_entry.reason
    assert mock_client.subscribers == []


async def test_an_entry_from_before_the_serial_records_its_udn_on_migration(
    hass, config_entry, mock_client
):
    """1.1 entries were keyed on the UDN and stored only the host."""
    assert config_entry.minor_version == 1
    config_entry.add_to_hass(hass)
    with patch("custom_components.libresync.LibreSyncClient", return_value=mock_client):
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    assert config_entry.minor_version == 2
    assert config_entry.unique_id == UDN
    assert config_entry.data[CONF_UDN] == UDN


async def test_the_serial_is_recorded_once_the_hub_says_it_and_the_key_never_moves(
    hass, config_entry, mock_client
):
    mock_client.state = FULL_STATE.evolve(serial=None)
    config_entry.add_to_hass(hass)
    with patch("custom_components.libresync.LibreSyncClient", return_value=mock_client):
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()
    assert CONF_SERIAL not in config_entry.data

    # Box 231 answers a moment after the ports are up, as it does on the hub.
    push(mock_client, FULL_STATE.evolve(serial=SERIAL))
    await hass.async_block_till_done()
    assert config_entry.data[CONF_SERIAL] == SERIAL
    assert config_entry.unique_id == UDN

    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, UDN)})
    assert device is not None
    assert device.serial_number == SERIAL

    # The model answers on its own frame too, and the device card follows it.
    push(mock_client, FULL_STATE.evolve(serial=SERIAL, model="Stereo Hub HT"))
    await hass.async_block_till_done()
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, UDN)})
    assert device is not None
    assert device.model == "Stereo Hub HT"


async def test_an_entry_from_a_newer_version_is_not_downgraded(hass, mock_client):
    entry = MockConfigEntry(domain=DOMAIN, unique_id=UDN, version=2, data={CONF_HOST: HOST})
    entry.add_to_hass(hass)
    with patch("custom_components.libresync.LibreSyncClient", return_value=mock_client):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.MIGRATION_ERROR


async def test_a_different_hub_at_the_same_address_does_not_take_the_entry_over(
    hass, mock_client, caplog
):
    """DHCP can hand the address to a second hub. Its serial is not adopted, or
    discovery of either hub would lead to this entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=SERIAL,
        minor_version=2,
        data={CONF_HOST: HOST, CONF_SERIAL: SERIAL, CONF_UDN: UDN},
    )
    mock_client.state = FULL_STATE.evolve(serial=SERIAL)
    entry.add_to_hass(hass)
    with patch("custom_components.libresync.LibreSyncClient", return_value=mock_client):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    push(mock_client, FULL_STATE.evolve(serial="OTHER0SERIAL00000000"))
    await hass.async_block_till_done()

    assert entry.data[CONF_SERIAL] == SERIAL
    [device] = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert device.serial_number == SERIAL
    assert "reports a different serial" in caplog.text


async def test_a_setup_that_fails_after_connecting_leaves_no_client_running(
    hass, config_entry, mock_client
):
    """Home Assistant does not unload an entry whose setup raised."""
    config_entry.add_to_hass(hass)
    with (
        patch("custom_components.libresync.LibreSyncClient", return_value=mock_client),
        patch(
            "homeassistant.config_entries.ConfigEntries.async_forward_entry_setups",
            side_effect=RuntimeError("boom"),
        ),
    ):
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    mock_client.async_disconnect.assert_awaited_once()
