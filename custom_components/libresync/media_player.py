"""The hub as a media player. No `off` and no `on` — see `switch.py` for power."""

import logging
from dataclasses import replace
from datetime import timedelta
from typing import Any

from aiolibresync import DeviceState, LibreSyncClient, NowPlaying, PlayState
from homeassistant.components.media_player import (
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
)

# From the `const` submodule rather than the package: `media_player` defines an
# `__all__` that omits these three, so `mypy --strict` refuses the re-export.
from homeassistant.components.media_player.const import (
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from . import LibreSyncConfigEntry
from .const import DOMAIN
from .entity import LibreSyncEntity, handle_errors

_LOGGER = logging.getLogger(__name__)

#: One action at a time: the default volume step reads the current level, and
#: two in flight at once would read the same one.
PARALLEL_UPDATES = 1

#: Playback arrives in `PlayState`'s values on both ports, and only these four
#: occur. `LOADING` is a cast handshake, which is what `BUFFERING` is for.
_STATES = {
    PlayState.PLAYING: MediaPlayerState.PLAYING,
    PlayState.PAUSED: MediaPlayerState.PAUSED,
    PlayState.IDLE: MediaPlayerState.IDLE,
    PlayState.LOADING: MediaPlayerState.BUFFERING,
}

#: Where the volume to restore on unmute is kept across a restart. An attribute
#: rather than storage, because `RestoreEntity` hands attributes back for free
#: and this is worth exactly one integer.
ATTR_MUTED_VOLUME = "libresync_muted_volume"

#: The hub pushes the position about once a second while playing. A push that
#: lands within this distance of where the frontend extrapolates it is not
#: written, so a playing track is not a state write, and a recorder row, every
#: second.
POSITION_TOLERANCE_MS = 1500


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LibreSyncConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    assert entry.unique_id is not None  # the config flow refuses an entry without one
    async_add_entities([LibreSyncMediaPlayer(entry.runtime_data, entry.unique_id)])


class LibreSyncMediaPlayer(LibreSyncEntity, MediaPlayerEntity, RestoreEntity):
    """Everything the hub plays, and everything that changes what it plays.

    **No `TURN_ON` and no `TURN_OFF`.** The hub's "power" is a stop that
    terminates the session and restores nothing, and on the wake path it arrives
    *last*, as a consequence of playback starting rather than its cause. A media
    player carrying it would report "off" while music was playing. It is a
    switch instead.
    """

    _attr_name = None  # this is the device's primary entity; it takes its name
    _attr_device_class = MediaPlayerDeviceClass.SPEAKER
    _attr_media_content_type = MediaType.MUSIC
    _attr_supported_features = (
        MediaPlayerEntityFeature.VOLUME_SET
        | MediaPlayerEntityFeature.VOLUME_STEP
        | MediaPlayerEntityFeature.VOLUME_MUTE
        | MediaPlayerEntityFeature.SELECT_SOURCE
        | MediaPlayerEntityFeature.PLAY
        | MediaPlayerEntityFeature.PAUSE
        | MediaPlayerEntityFeature.STOP
        | MediaPlayerEntityFeature.NEXT_TRACK
        | MediaPlayerEntityFeature.PREVIOUS_TRACK
    )

    def __init__(self, client: LibreSyncClient, unique_id_base: str) -> None:
        super().__init__(client, unique_id_base)
        self._attr_unique_id = unique_id_base
        #: The level to go back to on unmute, or None when we are not muting.
        self._muted_volume: int | None = None
        #: The state last written, to tell a position that only moved on.
        self._written = client.state
        self._take_position(client.state)

    # --- state ---------------------------------------------------------------

    @property
    def state(self) -> MediaPlayerState | None:
        """From `playback`, which is `02 21` byte 8 wherever it is known.

        Not from `play_state`: on eight of the ten inputs the renderer answers
        PLAYING to whatever is selected, connected or not, and goes on answering
        it after a return to Streaming. That was seen again on this hardware
        with the hub switched *off*.
        """
        playback = self.snapshot.playback
        return _STATES.get(playback) if playback is not None else None

    @property
    def source(self) -> str | None:
        source = self.snapshot.source
        return source.name if source is not None else None

    @property
    def source_list(self) -> list[str]:
        """The device's own list, in the device's own order.

        Never a static enum: a rebranded hub exposes a different set of inputs,
        and this list is what makes the integration work on one.
        """
        return [source.name for source in self.snapshot.sources]

    @property
    def volume_level(self) -> float | None:
        volume = self.snapshot.volume
        return volume / 100 if volume is not None else None

    @property
    def is_volume_muted(self) -> bool | None:
        """Ours or the device's — either one means no sound.

        The device's own flag can be set by something we cannot see and cannot
        clear: writing message box 63 is accepted, changes the value that reads
        back, and leaves the audio alone.
        """
        if self._muted_volume is not None:
            return True
        return self.snapshot.muted

    @property
    def media_title(self) -> str | None:
        return self._track.title

    @property
    def media_artist(self) -> str | None:
        return self._track.artist

    @property
    def media_album_name(self) -> str | None:
        return self._track.album

    @property
    def media_image_url(self) -> str | None:
        return self._track.artwork_url

    @property
    def _track(self) -> NowPlaying:
        return self.snapshot.now_playing or NowPlaying()

    @property
    def media_duration(self) -> int | None:
        duration = self._track.duration_ms
        return duration // 1000 if duration is not None else None

    @property
    def app_name(self) -> str | None:
        return self._track.app

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Carries the saved volume so `RestoreEntity` can hand it back."""
        if self._muted_volume is None:
            return None
        return {ATTR_MUTED_VOLUME: self._muted_volume}

    # --- commands ------------------------------------------------------------

    @handle_errors
    async def async_select_source(self, source: str) -> None:
        """Send the device's `ix`, never the position in the list.

        The two disagree: this hub reports Streaming as `ix` 0 at the *end* of
        its list, and the `02 21` source byte is a third numbering again.
        """
        for candidate in self.snapshot.sources:
            if candidate.name == source:
                await self._client.async_select_source(candidate.index)
                return
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="invalid_source",
            translation_placeholders={"source": source},
        )

    @handle_errors
    async def async_set_volume_level(self, volume: float) -> None:
        await self._client.async_set_volume(round(volume * 100))

    @handle_errors
    async def async_mute_volume(self, mute: bool) -> None:
        """Simulated: the hub has no mute to write, and Home Assistant fakes none.

        Muting saves the level and sets zero; unmuting restores it — unless the
        volume moved in the meantime, in which case `_handle_state` has already
        cancelled the simulation and there is nothing left to restore.
        """
        if mute:
            if self._muted_volume is None:
                self._muted_volume = self.snapshot.volume
            await self._client.async_set_volume(0)
            return
        restore, self._muted_volume = self._muted_volume, None
        if restore is not None:
            await self._client.async_set_volume(restore)
        elif self.snapshot.muted:
            # The hub is muted and we did not do it. Nothing this integration
            # can send clears that flag, so say so rather than fail in silence.
            _LOGGER.warning(
                "%s is muted by the device itself and cannot be unmuted from "
                "Home Assistant; use the remote or the vendor's app",
                self.entity_id,
            )

    @handle_errors
    async def async_media_play(self) -> None:
        await self._client.async_media_play()

    @handle_errors
    async def async_media_pause(self) -> None:
        await self._client.async_media_pause()

    @handle_errors
    async def async_media_stop(self) -> None:
        await self._client.async_media_stop()

    @handle_errors
    async def async_media_next_track(self) -> None:
        await self._client.async_media_next_track()

    @handle_errors
    async def async_media_previous_track(self) -> None:
        await self._client.async_media_previous_track()

    # --- lifecycle -----------------------------------------------------------

    @callback
    def _handle_state(self, state: DeviceState) -> None:
        """Write the new state, unless only the position moved, as extrapolated.

        Cancel the simulated mute if the volume moved away from zero. Someone
        else changed it — the remote, the vendor's app, another client on the
        shared bus — and restoring the old level later would overwrite the
        choice they just made.
        """
        if self._position_on_course(state):
            return
        if self._muted_volume is not None and state.volume not in (0, None):
            self._muted_volume = None
        # A write for another reason keeps the time the position was read.
        if state.position_ms != self._written.position_ms:
            self._take_position(state)
        self._written = state
        super()._handle_state(state)

    def _take_position(self, state: DeviceState) -> None:
        """Publish the position in seconds, with the time it was read.

        The library has already turned the `-1000` stop sentinel into None. The
        time is moved back by the fraction of a second the whole seconds leave
        out, so the frontend's extrapolation is exact.
        """
        position = state.position_ms
        if position is None:
            self._attr_media_position = None
            self._attr_media_position_updated_at = None
        else:
            self._attr_media_position = position // 1000
            self._attr_media_position_updated_at = dt_util.utcnow() - timedelta(
                milliseconds=position % 1000
            )

    def _position_on_course(self, state: DeviceState) -> bool:
        """Whether the position is all that changed, and where the frontend has it.

        The frontend extrapolates only while playing.
        """
        written = self._written
        shown = self._attr_media_position
        updated_at = self._attr_media_position_updated_at
        if (
            self.state is not MediaPlayerState.PLAYING
            or state.position_ms is None
            or shown is None
            or updated_at is None
            or replace(state, position_ms=written.position_ms) != written
        ):
            return False
        elapsed = dt_util.utcnow() - updated_at
        expected = shown * 1000 + elapsed.total_seconds() * 1000
        return abs(state.position_ms - expected) < POSITION_TOLERANCE_MS

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and (saved := last.attributes.get(ATTR_MUTED_VOLUME)):
            # Without this, a Home Assistant restart while muted strands the
            # user at volume zero with the old level gone.
            self._muted_volume = int(saved)
