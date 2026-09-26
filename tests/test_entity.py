"""What every entity shares: failed commands become errors a user can read."""

import pytest
from aiolibresync import ConfirmationTimeout, NotConnectedError
from homeassistant.exceptions import HomeAssistantError

from .test_media_player import setup


@pytest.mark.parametrize(
    ("error", "key"),
    [
        (NotConnectedError("control socket down"), "not_connected"),
        (ConfirmationTimeout("no echo"), "not_confirmed"),
    ],
)
@pytest.mark.parametrize(
    ("domain", "service", "data", "method"),
    [
        ("switch", "turn_on", {"entity_id": "switch.stereo_hub_power"}, "async_set_power"),
        (
            "select",
            "select_option",
            {"entity_id": "select.stereo_hub_eq_preset", "option": "2"},
            "async_select_eq_preset",
        ),
        (
            "media_player",
            "volume_set",
            {"entity_id": "media_player.stereo_hub", "volume_level": 0.3},
            "async_set_volume",
        ),
        (
            "media_player",
            "media_pause",
            {"entity_id": "media_player.stereo_hub"},
            "async_media_pause",
        ),
    ],
)
async def test_a_failed_command_is_a_translated_home_assistant_error(
    hass, config_entry, mock_client, error, key, domain, service, data, method
):
    """Silver `action-exceptions`: the library's exception never reaches the user raw."""
    await setup(hass, config_entry, mock_client)
    getattr(mock_client, method).side_effect = error
    with pytest.raises(HomeAssistantError) as raised:
        await hass.services.async_call(domain, service, data, blocking=True)
    assert raised.value.translation_key == key
    assert raised.value.translation_domain == "libresync"
