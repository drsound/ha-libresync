"""The dump that has to be enough to debug a hub nobody here owns."""

from homeassistant.const import CONF_HOST
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)

from custom_components.libresync.const import CONF_SERIAL, CONF_UDN, DOMAIN

from .conftest import HOST, SERIAL, UDN
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


async def test_the_serial_is_hidden_wherever_it_appears(hass, mock_client, hass_client):
    """A factory serial identifies one physical unit, like the address does."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=SERIAL,
        minor_version=2,
        data={CONF_HOST: HOST, CONF_SERIAL: SERIAL, CONF_UDN: UDN},
    )
    await setup(hass, entry, mock_client)
    result = await get_diagnostics_for_config_entry(hass, hass_client, entry)

    assert result["entry"]["unique_id"] == "**REDACTED**"
    assert result["entry"]["data"][CONF_SERIAL] == "**REDACTED**"
    assert result["entry"]["data"][CONF_UDN] == UDN
    assert SERIAL not in str(result)
