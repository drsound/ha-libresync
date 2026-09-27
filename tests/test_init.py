"""Setting the entry up, and taking it down again."""

from unittest.mock import patch

from aiolibresync import NotConnectedError
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.libresync.const import CONNECT_TIMEOUT, DOMAIN

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


async def _setup(hass, entry, mock_client) -> None:
    entry.add_to_hass(hass)
    with patch("custom_components.libresync.LibreSyncClient", return_value=mock_client):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


async def test_an_entry_from_1_1_is_already_keyed_on_the_udn(hass, config_entry, mock_client):
    """1.1 entries were keyed on the UDN and stored only the host."""
    assert config_entry.minor_version == 1
    await _setup(hass, config_entry, mock_client)

    assert config_entry.state is ConfigEntryState.LOADED
    assert config_entry.minor_version == 3
    assert config_entry.unique_id == UDN
    assert dict(config_entry.data) == {CONF_HOST: HOST}


async def test_an_entry_from_1_2_keyed_on_the_udn_keeps_only_the_host(hass, mock_client):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=UDN,
        minor_version=2,
        data={CONF_HOST: HOST, "serial": SERIAL, "udn": UDN},
    )
    await _setup(hass, entry, mock_client)

    assert entry.state is ConfigEntryState.LOADED
    assert entry.unique_id == UDN
    assert dict(entry.data) == {CONF_HOST: HOST}


async def test_an_entry_from_1_2_keyed_on_the_serial_moves_to_the_udn(hass, mock_client):
    """The device and the entities follow, so entity ids and history survive."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=SERIAL,
        minor_version=2,
        data={CONF_HOST: HOST, "serial": SERIAL, "udn": UDN},
    )
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, SERIAL)}
    )
    entities = er.async_get(hass)
    player = entities.async_get_or_create(
        "media_player", DOMAIN, SERIAL, config_entry=entry, suggested_object_id="stereo_hub"
    )
    power = entities.async_get_or_create(
        "switch", DOMAIN, f"{SERIAL}_power", config_entry=entry, suggested_object_id="hub_power"
    )
    await _setup(hass, entry, mock_client)

    assert entry.state is ConfigEntryState.LOADED
    assert entry.minor_version == 3
    assert entry.unique_id == UDN
    assert dict(entry.data) == {CONF_HOST: HOST}
    assert dr.async_get(hass).async_get(device.id).identifiers == {(DOMAIN, UDN)}
    assert entities.async_get(player.entity_id).unique_id == UDN
    assert entities.async_get(power.entity_id).unique_id == f"{UDN}_power"


async def test_an_entry_keyed_on_the_serial_with_no_udn_cannot_migrate(hass, mock_client):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=SERIAL,
        minor_version=2,
        data={CONF_HOST: HOST, "serial": SERIAL},
    )
    await _setup(hass, entry, mock_client)
    assert entry.state is ConfigEntryState.MIGRATION_ERROR


async def test_the_device_card_follows_the_serial_and_the_model(hass, config_entry, mock_client):
    mock_client.state = FULL_STATE.evolve(serial=None, model=None)
    await _setup(hass, config_entry, mock_client)

    # Box 231 answers a moment after the ports are up, as it does on the hub.
    push(mock_client, FULL_STATE.evolve(serial=SERIAL))
    await hass.async_block_till_done()
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, UDN)})
    assert device is not None
    assert device.serial_number == SERIAL
    assert device.model == "Stereo Hub"
    assert dict(config_entry.data) == {CONF_HOST: HOST}

    # The model answers on its own frame too, and the device card follows it.
    push(mock_client, FULL_STATE.evolve(serial=SERIAL, model="Stereo Hub HT"))
    await hass.async_block_till_done()
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, UDN)})
    assert device is not None
    assert device.model == "Stereo Hub HT"


async def test_the_device_keeps_its_model_and_serial_across_a_reload(
    hass, config_entry, mock_client
):
    """Entities are added before the hub answers, and must not blank the card."""
    mock_client.state = FULL_STATE.evolve(serial=SERIAL)
    await _setup(hass, config_entry, mock_client)

    mock_client.state = FULL_STATE.evolve(serial=None, model=None)
    await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()

    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, UDN)})
    assert device is not None
    assert device.serial_number == SERIAL
    assert device.model == "Stereo Hub"


async def test_an_entry_from_a_newer_version_is_not_downgraded(hass, mock_client):
    entry = MockConfigEntry(domain=DOMAIN, unique_id=UDN, version=2, data={CONF_HOST: HOST})
    await _setup(hass, entry, mock_client)
    assert entry.state is ConfigEntryState.MIGRATION_ERROR


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
