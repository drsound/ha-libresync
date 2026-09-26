"""The EQ preset."""

import pytest
from homeassistant.exceptions import ServiceValidationError

from .conftest import FULL_STATE, push
from .test_media_player import setup

ENTITY = "select.stereo_hub_eq_preset"


async def test_the_preset_reads_and_writes(hass, config_entry, mock_client):
    await setup(hass, config_entry, mock_client)
    assert hass.states.get(ENTITY).state == "3"
    assert hass.states.get(ENTITY).attributes["options"] == ["1", "2", "3"]

    await hass.services.async_call(
        "select", "select_option", {"entity_id": ENTITY, "option": "2"}, blocking=True
    )
    mock_client.async_select_eq_preset.assert_awaited_once_with(2)


async def test_a_preset_the_hub_has_not_reported_is_unknown(hass, config_entry, mock_client):
    await setup(hass, config_entry, mock_client)
    push(mock_client, FULL_STATE.evolve(eq_preset=None))
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY).state == "unknown"


async def test_preset_0_is_the_app_editor_and_is_offered_only_while_current(
    hass, config_entry, mock_client
):
    """Preset 0 is the vendor app's editor slot, measured on 2026-09-26.

    The app selects it when its editor opens. It is not a fault, so it must not
    read `unknown`, and it is not something to configure from here, so it must
    not be offered once the hub has left it.
    """
    await setup(hass, config_entry, mock_client)
    push(mock_client, FULL_STATE.evolve(eq_preset=0))
    await hass.async_block_till_done()
    state = hass.states.get(ENTITY)
    assert state.state == "editor"
    assert state.attributes["options"] == ["1", "2", "3", "editor"]

    push(mock_client, FULL_STATE.evolve(eq_preset=2))
    await hass.async_block_till_done()
    state = hass.states.get(ENTITY)
    assert state.state == "2"
    assert state.attributes["options"] == ["1", "2", "3"]


async def test_choosing_the_editor_sends_nothing(hass, config_entry, mock_client):
    await setup(hass, config_entry, mock_client)
    push(mock_client, FULL_STATE.evolve(eq_preset=0))
    await hass.async_block_till_done()

    await hass.services.async_call(
        "select", "select_option", {"entity_id": ENTITY, "option": "editor"}, blocking=True
    )
    mock_client.async_select_eq_preset.assert_not_awaited()


async def test_the_editor_cannot_be_chosen_when_it_is_not_current(hass, config_entry, mock_client):
    await setup(hass, config_entry, mock_client)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "select", "select_option", {"entity_id": ENTITY, "option": "editor"}, blocking=True
        )
    mock_client.async_select_eq_preset.assert_not_awaited()


async def test_a_preset_outside_the_known_range_is_unknown(hass, config_entry, mock_client):
    """Not observed. A firmware with a fourth preset must not break the select."""
    await setup(hass, config_entry, mock_client)
    push(mock_client, FULL_STATE.evolve(eq_preset=4))
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY).state == "unknown"
