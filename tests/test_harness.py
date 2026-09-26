"""If this fails, nothing below it means anything."""

from homeassistant.core import HomeAssistant


async def test_home_assistant_starts(hass: HomeAssistant) -> None:
    assert hass.state is not None


def test_the_library_is_importable() -> None:
    from aiolibresync import DeviceState, PlayState

    assert DeviceState().playback is None
    assert PlayState.PLAYING == 0
