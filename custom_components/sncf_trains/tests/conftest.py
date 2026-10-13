"""Shared pytest fixtures."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Let Home Assistant load custom_components/sncf_trains in every test."""
    return


@pytest.fixture(autouse=True)
def stub_frontend(hass):
    """Stand in for the frontend/http components.

    The integration depends on ``frontend`` to register its Lovelace card,
    which needs the real frontend package. Tests only exercise the
    integration's own logic, so the dependency is marked as loaded and the
    card registration is a no-op.
    """
    hass.config.components.update({"frontend", "http"})
    hass.http = MagicMock(async_register_static_paths=AsyncMock())
    with patch("custom_components.sncf_trains.add_extra_js_url"):
        yield
