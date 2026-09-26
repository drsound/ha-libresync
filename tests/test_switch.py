"""Three switches, and the difference between off and not-yet-known."""

import pytest

from .conftest import FULL_STATE, push
from .test_media_player import setup


@pytest.mark.parametrize(
    ("entity_id", "field", "method"),
    [
        ("switch.stereo_hub_power", "power", "async_set_power"),
        ("switch.stereo_hub_room_correction", "room_correction", "async_set_room_correction"),
        ("switch.stereo_hub_manual_eq", "manual_eq", "async_set_manual_eq"),
    ],
)
async def test_each_switch_reads_its_field_and_writes_it(
    hass, config_entry, mock_client, entity_id, field, method
):
    await setup(hass, config_entry, mock_client)
    push(mock_client, FULL_STATE.evolve(**{field: True}))
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "on"

    await hass.services.async_call("switch", "turn_off", {"entity_id": entity_id}, blocking=True)
    getattr(mock_client, method).assert_awaited_once_with(False)

    await hass.services.async_call("switch", "turn_on", {"entity_id": entity_id}, blocking=True)
    assert getattr(mock_client, method).await_args.args == (True,)


async def test_a_field_the_hub_has_not_reported_is_unknown_not_off(hass, config_entry, mock_client):
    """`None` means "not yet known", and rendering it as off would invent a fact.

    Room correction is announced by nobody at all, so it is the field most
    likely to sit unknown for a poll interval.
    """
    await setup(hass, config_entry, mock_client)
    push(mock_client, FULL_STATE.evolve(room_correction=None))
    await hass.async_block_till_done()
    assert hass.states.get("switch.stereo_hub_room_correction").state == "unknown"


async def test_room_correction_and_manual_eq_are_independent(hass, config_entry, mock_client):
    """Not exclusive: both can be on at once, which is why there are two."""
    await setup(hass, config_entry, mock_client)
    push(mock_client, FULL_STATE.evolve(room_correction=True, manual_eq=True))
    await hass.async_block_till_done()
    assert hass.states.get("switch.stereo_hub_room_correction").state == "on"
    assert hass.states.get("switch.stereo_hub_manual_eq").state == "on"
