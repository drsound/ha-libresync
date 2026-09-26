"""Whether sound is coming out, which is not the same as a session being open."""

import pytest
from aiolibresync import PlayState

from .conftest import FULL_STATE, push
from .test_media_player import setup

ENTITY = "binary_sensor.stereo_hub_audio"


@pytest.mark.parametrize(
    ("audio_state", "expected"),
    [
        (PlayState.PLAYING, "on"),
        (PlayState.PAUSED, "off"),
        (PlayState.LOADING, "off"),
        (PlayState.IDLE, "off"),
        (None, "unknown"),
    ],
)
async def test_it_is_on_only_while_sound_is_coming_out(
    hass, config_entry, mock_client, audio_state, expected
):
    """Paused and loading are both silence, and the byte reports them
    distinctly — so the check is equality with PLAYING, not truthiness."""
    await setup(hass, config_entry, mock_client)
    push(mock_client, FULL_STATE.evolve(audio_state=audio_state))
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY).state == expected


async def test_it_disagrees_with_the_transport_when_the_transport_lies(
    hass, config_entry, mock_client
):
    """The case the whole design exists for, as one assertion."""
    await setup(hass, config_entry, mock_client)
    push(mock_client, FULL_STATE.evolve(play_state=PlayState.PLAYING, audio_state=PlayState.IDLE))
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY).state == "off"
