"""The player, and the two things about it that are easy to get wrong."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from aiolibresync import PlayState
from homeassistant.components.media_player import MediaPlayerState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.exceptions import ServiceValidationError

from .conftest import FULL_STATE, push

ENTITY = "media_player.stereo_hub"


async def setup(hass, config_entry, mock_client):
    """Load the entry with a client that never opens a socket."""
    config_entry.add_to_hass(hass)
    with patch("custom_components.libresync.LibreSyncClient", return_value=mock_client):
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()


async def test_state_comes_from_the_audio_and_not_the_transport(hass, config_entry, mock_client):
    """A physical input with nothing plugged in: 7777 says PLAYING for ever.

    Measured 2026-08-16 over five USB selections, and seen again on the hub the
    same evening with the power switched *off*. The entity believes byte 8.
    """
    await setup(hass, config_entry, mock_client)
    push(mock_client, FULL_STATE.evolve(play_state=PlayState.PLAYING, audio_state=PlayState.IDLE))
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY).state == MediaPlayerState.IDLE


async def test_loading_is_buffering(hass, config_entry, mock_client):
    await setup(hass, config_entry, mock_client)
    push(mock_client, FULL_STATE.evolve(audio_state=PlayState.LOADING))
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY).state == MediaPlayerState.BUFFERING


async def test_a_disconnected_hub_is_unavailable(hass, config_entry, mock_client):
    await setup(hass, config_entry, mock_client)
    push(mock_client, FULL_STATE.evolve(available=False))
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY).state == STATE_UNAVAILABLE


async def test_a_powered_down_hub_is_not_unavailable(hass, config_entry, mock_client):
    """Port 7777 answers while the hub is switched off, so `unavailable` would
    be a lie. Only the socket decides that one."""
    await setup(hass, config_entry, mock_client)
    push(mock_client, FULL_STATE.evolve(power=False, audio_state=PlayState.IDLE))
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY).state == MediaPlayerState.IDLE


async def test_the_player_offers_no_on_or_off(hass, config_entry, mock_client):
    """Power is a switch. A media player carrying it would say off while music
    played, because on the wake path power arrives last."""
    from homeassistant.components.media_player import MediaPlayerEntityFeature

    await setup(hass, config_entry, mock_client)
    features = hass.states.get(ENTITY).attributes["supported_features"]
    assert not features & MediaPlayerEntityFeature.TURN_ON
    assert not features & MediaPlayerEntityFeature.TURN_OFF


async def test_the_source_list_is_the_devices_own(hass, config_entry, mock_client):
    await setup(hass, config_entry, mock_client)
    state = hass.states.get(ENTITY)
    assert state.attributes["source_list"] == ["USB", "HDMI", "Streaming"]
    assert state.attributes["source"] == "Streaming"


async def test_selecting_a_source_sends_the_json_index_not_the_position(
    hass, config_entry, mock_client
):
    """`ix` is the device's own numbering, and the list is not sorted by it —
    HDMI is second in the list and `ix` 6."""
    await setup(hass, config_entry, mock_client)
    await hass.services.async_call(
        "media_player",
        "select_source",
        {"entity_id": ENTITY, "source": "HDMI"},
        blocking=True,
    )
    mock_client.async_select_source.assert_awaited_once_with(6)


async def test_a_source_the_hub_does_not_report_is_refused(hass, config_entry, mock_client):
    await setup(hass, config_entry, mock_client)
    with pytest.raises(ServiceValidationError) as raised:
        await hass.services.async_call(
            "media_player",
            "select_source",
            {"entity_id": ENTITY, "source": "Phono"},
            blocking=True,
        )
    assert raised.value.translation_key == "invalid_source"
    mock_client.async_select_source.assert_not_awaited()


@pytest.mark.parametrize(
    ("service", "method"),
    [
        ("media_play", "async_media_play"),
        ("media_pause", "async_media_pause"),
        ("media_stop", "async_media_stop"),
        ("media_next_track", "async_media_next_track"),
        ("media_previous_track", "async_media_previous_track"),
    ],
)
async def test_transport_calls_reach_the_client(hass, config_entry, mock_client, service, method):
    await setup(hass, config_entry, mock_client)
    await hass.services.async_call("media_player", service, {"entity_id": ENTITY}, blocking=True)
    getattr(mock_client, method).assert_awaited_once()


# --- volume and the mute the device does not have ----------------------------


async def test_volume_is_reported_as_a_fraction(hass, config_entry, mock_client):
    await setup(hass, config_entry, mock_client)
    assert hass.states.get(ENTITY).attributes["volume_level"] == 0.28


async def test_setting_the_volume_sends_whole_percent(hass, config_entry, mock_client):
    await setup(hass, config_entry, mock_client)
    await hass.services.async_call(
        "media_player",
        "volume_set",
        {"entity_id": ENTITY, "volume_level": 0.415},
        blocking=True,
    )
    mock_client.async_set_volume.assert_awaited_once_with(42)


async def test_mute_saves_the_volume_and_gives_it_back(hass, config_entry, mock_client):
    """The device has no writable mute, so this is volume 0 and back again."""
    await setup(hass, config_entry, mock_client)
    await hass.services.async_call(
        "media_player",
        "volume_mute",
        {"entity_id": ENTITY, "is_volume_muted": True},
        blocking=True,
    )
    mock_client.async_set_volume.assert_awaited_once_with(0)

    push(mock_client, FULL_STATE.evolve(volume=0))
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY).attributes["is_volume_muted"] is True

    await hass.services.async_call(
        "media_player",
        "volume_mute",
        {"entity_id": ENTITY, "is_volume_muted": False},
        blocking=True,
    )
    assert mock_client.async_set_volume.await_args.args == (28,)


async def test_the_devices_own_mute_flag_also_reads_as_muted(hass, config_entry, mock_client):
    """Something else can mute this hub, and we would not know we had not."""
    await setup(hass, config_entry, mock_client)
    push(mock_client, FULL_STATE.evolve(muted=True))
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY).attributes["is_volume_muted"] is True


async def test_an_external_volume_change_cancels_the_simulation(hass, config_entry, mock_client):
    """Someone reaches for the remote while Home Assistant thinks it is muting.

    Restoring the old level afterwards would overwrite the choice they just
    made, so the saved value is dropped instead.
    """
    await setup(hass, config_entry, mock_client)
    await hass.services.async_call(
        "media_player",
        "volume_mute",
        {"entity_id": ENTITY, "is_volume_muted": True},
        blocking=True,
    )
    push(mock_client, FULL_STATE.evolve(volume=45))
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY).attributes["is_volume_muted"] is False

    mock_client.async_set_volume.reset_mock()
    await hass.services.async_call(
        "media_player",
        "volume_mute",
        {"entity_id": ENTITY, "is_volume_muted": False},
        blocking=True,
    )
    mock_client.async_set_volume.assert_not_awaited()


async def test_the_metadata_reaches_the_card(hass, config_entry, mock_client):
    from aiolibresync import NowPlaying

    await setup(hass, config_entry, mock_client)
    push(
        mock_client,
        FULL_STATE.evolve(
            now_playing=NowPlaying(
                title="La Sentencia",
                artist="Melissa Aldana",
                album="La Sentencia",
                app="TIDAL",
                duration_ms=277597,
            ),
            position_ms=32567,
        ),
    )
    await hass.async_block_till_done()
    attributes = hass.states.get(ENTITY).attributes
    assert attributes["media_title"] == "La Sentencia"
    assert attributes["media_artist"] == "Melissa Aldana"
    assert attributes["media_duration"] == 277
    assert attributes["media_position"] == 32


async def test_a_position_that_moves_as_extrapolated_is_not_written(
    hass, config_entry, mock_client, freezer
):
    """The hub pushes the position every second while playing. Writing each one
    would record a state row per second for as long as music plays."""
    await setup(hass, config_entry, mock_client)
    playing = FULL_STATE.evolve(
        play_state=PlayState.PLAYING, audio_state=PlayState.PLAYING, position_ms=10000
    )
    push(mock_client, playing)
    await hass.async_block_till_done()
    written = hass.states.get(ENTITY)
    assert written.attributes["media_position"] == 10

    freezer.tick(timedelta(seconds=1))
    push(mock_client, playing.evolve(position_ms=11000))
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY).last_updated == written.last_updated

    # A seek is written, with the time it was read.
    push(mock_client, playing.evolve(position_ms=90000))
    await hass.async_block_till_done()
    attributes = hass.states.get(ENTITY).attributes
    assert attributes["media_position"] == 90
    assert attributes["media_position_updated_at"] > written.attributes["media_position_updated_at"]


async def test_a_position_that_moves_while_paused_is_written(
    hass, config_entry, mock_client, freezer
):
    """The frontend extrapolates only while playing."""
    await setup(hass, config_entry, mock_client)
    push(mock_client, FULL_STATE.evolve(audio_state=PlayState.PAUSED, position_ms=10000))
    await hass.async_block_till_done()

    freezer.tick(timedelta(seconds=1))
    push(mock_client, FULL_STATE.evolve(audio_state=PlayState.PAUSED, position_ms=11000))
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY).attributes["media_position"] == 11
