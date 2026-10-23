"""A newer hub, whose port 7777 is wrapped in TLS: run on port 50006 alone."""

from unittest.mock import AsyncMock, patch

from aiolibresync import LssdpInfo
from homeassistant.components.media_player import MediaPlayerEntityFeature, MediaPlayerState
from homeassistant.const import CONF_HOST
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.libresync.const import CONF_MODULE, DOMAIN, TLS_POLL_INTERVAL

from .conftest import FULL_STATE, HOST, UDN, push

ENTITY = "media_player.stereo_hub"


async def setup(hass, mock_client, module, lssdp=None):
    mock_client.media = module != "LS10"
    data = {CONF_HOST: HOST} | ({CONF_MODULE: module} if module else {})
    entry = MockConfigEntry(domain=DOMAIN, title="Stereo Hub", unique_id=UDN, data=data)
    entry.add_to_hass(hass)
    with (
        patch(
            "custom_components.libresync.LibreSyncClient", return_value=mock_client
        ) as constructor,
        patch("custom_components.libresync.async_query_lssdp", AsyncMock(return_value=lssdp)),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry, constructor


async def test_an_ls10_hub_runs_without_7777(hass, mock_client):
    _entry, constructor = await setup(hass, mock_client, "LS10")

    assert constructor.call_args.kwargs == {"media": False, "poll_interval": TLS_POLL_INTERVAL}
    features = hass.states.get(ENTITY).attributes["supported_features"]
    assert features == MediaPlayerEntityFeature.SELECT_SOURCE


async def test_an_ls10_hub_gets_power_and_source_only(hass, mock_client, entity_registry):
    entry, _constructor = await setup(hass, mock_client, "LS10")

    entities = {
        e.entity_id for e in er.async_entries_for_config_entry(entity_registry, entry.entry_id)
    }
    assert entities == {ENTITY, "switch.stereo_hub_power"}


async def test_an_ls10_hub_is_on_or_off_by_its_power(hass, mock_client):
    await setup(hass, mock_client, "LS10")
    assert hass.states.get(ENTITY).state == MediaPlayerState.ON

    push(mock_client, FULL_STATE.evolve(power=False))
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY).state == MediaPlayerState.OFF


async def test_an_entry_without_a_module_keeps_both_ports_if_lssdp_is_silent(hass, mock_client):
    entry, constructor = await setup(hass, mock_client, None)

    assert constructor.call_args.kwargs["media"] is True
    assert CONF_MODULE not in entry.data


async def test_an_entry_without_a_module_learns_it_at_setup(hass, mock_client):
    """An LS10 hub added while its LSSDP reply was lost is not left on 7777."""
    entry, constructor = await setup(hass, mock_client, None, LssdpInfo(module="LS10"))

    assert entry.data[CONF_MODULE] == "LS10"
    assert constructor.call_args.kwargs["media"] is False
