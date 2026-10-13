"""Tests for error classification and Home Assistant repairs (#7)."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers import issue_registry as ir

from custom_components.sncf_trains.api import (
    SncfApiClient,
    SncfInvalidJourneyError,
    SncfQuotaError,
)
from custom_components.sncf_trains.const import (
    CONF_API_KEY,
    CONF_FROM,
    CONF_TIME_END,
    CONF_TIME_START,
    CONF_TO,
    DOMAIN,
)
from custom_components.sncf_trains.coordinator import (
    FAILURES_BEFORE_ISSUE,
    SncfUpdateCoordinator,
)

# --- API client ---------------------------------------------------------------


class _Resp:
    def __init__(self, status, body):
        self.status = status
        self._body = body

    async def json(self, content_type="application/json"):
        return self._body

    def raise_for_status(self):
        if self.status >= 400:
            raise AssertionError("raise_for_status should not be reached")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _client(status, body) -> SncfApiClient:
    session = MagicMock()
    session.get = MagicMock(return_value=_Resp(status, body))
    return SncfApiClient(session, "key")


@pytest.mark.asyncio
async def test_no_solution_means_no_train_not_an_error():
    client = _client(404, {"error": {"id": "no_solution", "message": "no solution"}})

    assert (
        await client.fetch_journeys("stop_area:a", "stop_area:b", "20261014T073000")
        == []
    )


@pytest.mark.asyncio
async def test_unknown_object_is_an_invalid_journey():
    client = _client(
        404,
        {"error": {"id": "unknown_object", "message": "Invalid id : admin:fr:31446"}},
    )

    with pytest.raises(SncfInvalidJourneyError) as err:
        await client.fetch_journeys("admin:fr:31446", "stop_area:b", "20261014T073000")

    assert err.value.error_id == "unknown_object"
    assert "admin:fr:31446" in err.value.message


@pytest.mark.asyncio
async def test_quota_error_is_a_runtime_error():
    client = _client(429, {})

    with pytest.raises(SncfQuotaError):
        await client.fetch_journeys("stop_area:a", "stop_area:b", "20261014T073000")
    assert issubclass(SncfQuotaError, RuntimeError)


# --- Coordinator repairs ------------------------------------------------------


def _coordinator(hass) -> SncfUpdateCoordinator:
    subentry = MagicMock()
    subentry.title = "Trajet: A → B"
    subentry.data = {
        CONF_FROM: "stop_area:a",
        CONF_TO: "stop_area:b",
        CONF_TIME_START: "07:30",
        CONF_TIME_END: "09:00",
    }
    entry = MagicMock(spec=ConfigEntry)
    entry.data = {CONF_API_KEY: "key"}
    entry.options = {}
    entry.subentries = {"sub": subentry}
    coordinator = SncfUpdateCoordinator(hass, entry)
    coordinator.api_client = AsyncMock()
    return coordinator


def _now():
    return patch(
        "custom_components.sncf_trains.coordinator.dt_util.now",
        return_value=datetime(2026, 10, 14, 7, 0),
    )


@pytest.mark.asyncio
async def test_invalid_journey_raises_repair_without_retry(hass):
    coordinator = _coordinator(hass)
    coordinator.api_client.fetch_journeys = AsyncMock(
        side_effect=SncfInvalidJourneyError("unknown_object", "Invalid id")
    )

    with _now(), patch("custom_components.sncf_trains.coordinator.asyncio.sleep"):
        data = await coordinator._async_update_data()

    assert data == {"sub": []}
    # No retries for an invalid journey (+1 call for the planned timetable is
    # not made either since we stop before it).
    assert coordinator.api_client.fetch_journeys.await_count == 1
    issue = ir.async_get(hass).async_get_issue(DOMAIN, "invalid_journey_sub")
    assert issue is not None
    assert issue.translation_placeholders["reason"] == "Invalid id"

    # Fixed: the repair disappears.
    coordinator.api_client.fetch_journeys = AsyncMock(return_value=[])
    with _now():
        await coordinator._async_update_data()
    assert ir.async_get(hass).async_get_issue(DOMAIN, "invalid_journey_sub") is None


@pytest.mark.asyncio
async def test_outage_keeps_data_and_raises_repair_after_several_failures(hass):
    coordinator = _coordinator(hass)
    good = [{"id": "journey_1", "nb_transfers": 0}]
    coordinator.data = {"sub": good}
    coordinator.api_client.fetch_journeys = AsyncMock(return_value=None)

    with _now(), patch("custom_components.sncf_trains.coordinator.asyncio.sleep"):
        for attempt in range(1, FAILURES_BEFORE_ISSUE + 1):
            data = await coordinator._async_update_data()
            # Last known data is kept during the outage.
            assert data == {"sub": good}
            issue = ir.async_get(hass).async_get_issue(DOMAIN, "api_unavailable")
            assert (issue is not None) == (attempt >= FAILURES_BEFORE_ISSUE)

    assert issue.translation_key == "api_unavailable"

    # API back: repair cleared.
    coordinator.api_client.fetch_journeys = AsyncMock(return_value=good)
    with _now():
        await coordinator._async_update_data()
    assert ir.async_get(hass).async_get_issue(DOMAIN, "api_unavailable") is None


@pytest.mark.asyncio
async def test_quota_repair_uses_quota_text(hass):
    coordinator = _coordinator(hass)
    coordinator.api_client.fetch_journeys = AsyncMock(
        side_effect=SncfQuotaError("Quota exceeded")
    )

    with _now(), patch("custom_components.sncf_trains.coordinator.asyncio.sleep"):
        for _ in range(FAILURES_BEFORE_ISSUE):
            await coordinator._async_update_data()

    issue = ir.async_get(hass).async_get_issue(DOMAIN, "api_unavailable")
    assert issue.translation_key == "quota_exceeded"
