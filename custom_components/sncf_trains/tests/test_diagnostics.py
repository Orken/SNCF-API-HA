"""Tests for the diagnostics download."""

from homeassistant import config_entries
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sncf_trains.const import CONF_API_KEY, DOMAIN
from custom_components.sncf_trains.diagnostics import (
    async_get_config_entry_diagnostics,
)


async def test_diagnostics_when_setup_failed(hass):
    """API down at boot: setup failed and runtime_data was never set."""
    entry = MockConfigEntry(
        version=1,
        minor_version=2,
        domain=DOMAIN,
        title="Trains SNCF",
        data={CONF_API_KEY: "secret"},
        source=config_entries.SOURCE_USER,
        unique_id="sncf_trains",
    )
    entry.add_to_hass(hass)

    data = await async_get_config_entry_diagnostics(hass, entry)

    assert data["coordinator"]["available"] is False
    assert data["config_entry"]["data"][CONF_API_KEY] == "**REDACTED**"
