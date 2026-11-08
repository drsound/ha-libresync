"""Setting the entry up, and taking it down again."""

import asyncio
import logging
from dataclasses import replace
from unittest.mock import patch

import pytest
from aiolibresync import NotConnectedError
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.libresync.const import CONNECT_TIMEOUT, DOMAIN

from .conftest import FULL_STATE, HOST, LS9, SERIAL, UDN, push


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


def _device(hass, entry) -> dr.DeviceEntry:
    """The entry's one device. Identifiers are no longer unique across entries."""
    [device] = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    return device


async def _setup(hass, entry, mock_client) -> None:
    entry.add_to_hass(hass)
    with patch("custom_components.libresync.LibreSyncClient", return_value=mock_client):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


async def test_the_device_card_follows_the_model_and_never_shows_the_serial(
    hass, config_entry, mock_client
):
    mock_client.state = FULL_STATE.evolve(serial=None, model=None)
    await _setup(hass, config_entry, mock_client)

    # Box 231 answers a moment after the ports are up, as it does on the hub.
    # Its serial is the module's production code, not the one on the label.
    push(mock_client, FULL_STATE.evolve(serial=SERIAL))
    await hass.async_block_till_done()
    device = _device(hass, config_entry)
    assert device is not None
    assert device.serial_number is None
    assert device.model == "Stereo Hub"
    assert dict(config_entry.data) == {CONF_HOST: HOST}

    # The model answers on its own frame too, and the device card follows it.
    push(mock_client, FULL_STATE.evolve(serial=SERIAL, model="Stereo Hub HT"))
    await hass.async_block_till_done()
    device = _device(hass, config_entry)
    assert device is not None
    assert device.model == "Stereo Hub HT"


async def test_the_device_keeps_its_model_across_a_reload(hass, config_entry, mock_client):
    """Entities are added before the hub answers, and must not blank the card."""
    await _setup(hass, config_entry, mock_client)

    mock_client.state = FULL_STATE.evolve(serial=None, model=None)
    with patch("custom_components.libresync.LibreSyncClient", return_value=mock_client):
        await hass.config_entries.async_reload(config_entry.entry_id)
        await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.LOADED

    device = _device(hass, config_entry)
    assert device is not None
    assert device.model == "Stereo Hub"


async def test_a_serial_left_by_an_older_version_is_cleared(hass, config_entry, mock_client):
    config_entry.add_to_hass(hass)
    dr.async_get(hass).async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={(DOMAIN, UDN)},
        serial_number=SERIAL,
    )
    with patch("custom_components.libresync.LibreSyncClient", return_value=mock_client):
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    device = _device(hass, config_entry)
    assert device is not None
    assert device.serial_number is None


async def test_an_entry_from_a_newer_version_is_not_downgraded(hass, mock_client):
    entry = MockConfigEntry(domain=DOMAIN, unique_id=UDN, version=2, data={CONF_HOST: HOST})
    await _setup(hass, entry, mock_client)
    assert entry.state is ConfigEntryState.MIGRATION_ERROR


async def test_the_device_takes_the_name_the_hub_announced(hass, mock_client):
    """The entry's title, which discovery and the manual path take from the hub."""
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=UDN, title="Living room", data={CONF_HOST: HOST}
    )
    await _setup(hass, entry, mock_client)

    device = _device(hass, entry)
    assert device is not None
    assert device.name == "Living room"
    assert hass.states.get("media_player.living_room") is not None


async def test_an_outage_is_logged_once_when_it_starts_and_once_when_it_ends(
    hass, config_entry, mock_client, caplog
):
    await _setup(hass, config_entry, mock_client)
    caplog.set_level(logging.INFO, logger="custom_components.libresync")
    caplog.clear()

    down = FULL_STATE.evolve(available=False)
    for state in (down, down.evolve(volume=10), down):
        push(mock_client, state)
    for state in (FULL_STATE, FULL_STATE.evolve(volume=10)):
        push(mock_client, state)
    await hass.async_block_till_done()

    assert [r.getMessage() for r in caplog.records if r.name == "custom_components.libresync"] == [
        f"The hub at {HOST} is unavailable",
        f"The hub at {HOST} is back online",
    ]
    assert all(
        r.levelno == logging.INFO for r in caplog.records if r.name == "custom_components.libresync"
    )


async def _setup_with(hass, entry, client) -> None:
    entry.add_to_hass(hass)
    with patch("custom_components.libresync.LibreSyncClient", return_value=client):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


@pytest.mark.parametrize(
    "connect_error", [NotConnectedError("reset"), None], ids=["fails", "connects"]
)
async def test_an_ls10_hub_fails_setup_instead_of_retrying(
    hass, config_entry, mock_client, mock_lssdp, connect_error
):
    """Its 7777 never accepts this version's session, so retrying would go on
    for ever. Refused whether or not the connection happened to open."""
    mock_lssdp.return_value = replace(LS9, module="LS10")
    mock_client.async_connect.side_effect = connect_error
    await _setup_with(hass, config_entry, mock_client)

    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    assert "LS10" in config_entry.reason
    assert "pre-release" in config_entry.reason
    mock_client.async_disconnect.assert_awaited_once()


async def test_a_hub_silent_on_lssdp_is_retried_as_before(
    hass, config_entry, mock_client, mock_lssdp
):
    mock_lssdp.return_value = None
    mock_client.async_connect.side_effect = NotConnectedError("no answer")
    await _setup_with(hass, config_entry, mock_client)
    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_an_unexpected_lssdp_error_disconnects_and_fails(
    hass, config_entry, mock_client, mock_lssdp
):
    mock_lssdp.side_effect = RuntimeError("boom")
    await _setup_with(hass, config_entry, mock_client)
    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    mock_client.async_disconnect.assert_awaited_once()


async def test_an_unexpected_connect_error_fails_setup(hass, config_entry, mock_client):
    mock_client.async_connect.side_effect = RuntimeError("boom")
    await _setup_with(hass, config_entry, mock_client)
    assert config_entry.state is ConfigEntryState.SETUP_ERROR


async def test_a_setup_cancelled_mid_query_disconnects(hass, config_entry, mock_client, mock_lssdp):
    """The connection can open while LSSDP is still pending."""
    pending = asyncio.Event()

    async def never_answers(host: str) -> None:
        pending.set()
        await asyncio.Event().wait()

    mock_lssdp.side_effect = never_answers
    config_entry.add_to_hass(hass)
    with patch("custom_components.libresync.LibreSyncClient", return_value=mock_client):
        setup = hass.async_create_task(hass.config_entries.async_setup(config_entry.entry_id))
        await pending.wait()
        setup.cancel()
        with pytest.raises(asyncio.CancelledError):
            await setup
    mock_client.async_disconnect.assert_awaited_once()
