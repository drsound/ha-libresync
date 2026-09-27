"""The dump that has to be enough to debug a hub nobody here owns."""

from homeassistant.const import CONF_HOST
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)

from .test_media_player import setup


async def test_diagnostics_carry_the_state_and_hide_the_address(
    hass, config_entry, mock_client, hass_client
):
    await setup(hass, config_entry, mock_client)
    result = await get_diagnostics_for_config_entry(hass, hass_client, config_entry)

    assert "client" in result
    assert result["entry"]["data"][CONF_HOST] == "**REDACTED**"
    # The UDN stays: it is what tells two hubs apart in one bug report.
    assert result["entry"]["unique_id"].startswith("uuid:")
