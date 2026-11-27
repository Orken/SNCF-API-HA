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
    CONF_ARRIVAL_NAME,
    CONF_DEPARTURE_NAME,
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


@pytest.mark.asyncio
@pytest.mark.parametrize("data_freshness", ["realtime", "base_schedule"])
async def test_journeys_request_asks_for_direct_trains_only(data_freshness):
    """Journeys with a change are dropped: they must not use up the count.

    Otherwise 10 journeys with 3 changes leave only 7 direct trains, and the
    range ends at the 7th train instead of the 10th.
    """
    session = MagicMock()
    session.get = MagicMock(return_value=_Resp(200, {"journeys": []}))
    client = SncfApiClient(session, "key")

    await client.fetch_journeys(
        "stop_area:a",
        "stop_area:b",
        "20261014T073000",
        count=10,
        data_freshness=data_freshness,
    )

    params = session.get.call_args.kwargs["params"]
    assert params["max_nb_transfers"] == "0"
    assert params["count"] == "10"
    assert params["data_freshness"] == data_freshness


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
async def test_invalid_journey_after_outage_clears_unavailable_repair(hass):
    """The API answers again, even to refuse the journey: it is reachable."""
    coordinator = _coordinator(hass)
    coordinator.api_client.fetch_journeys = AsyncMock(return_value=None)
    with _now(), patch("custom_components.sncf_trains.coordinator.asyncio.sleep"):
        for _ in range(FAILURES_BEFORE_ISSUE):
            await coordinator._async_update_data()
    assert ir.async_get(hass).async_get_issue(DOMAIN, "api_unavailable")

    coordinator.api_client.fetch_journeys = AsyncMock(
        side_effect=SncfInvalidJourneyError("unknown_object", "Invalid id")
    )
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


# --- Station id changed -------------------------------------------------------


def _coordinator_with_names(hass) -> SncfUpdateCoordinator:
    coordinator = _coordinator(hass)
    subentry = coordinator.entry.subentries["sub"]
    subentry.data = {
        **subentry.data,
        CONF_FROM: "stop_area:SNCF:87611004",
        CONF_TO: "stop_area:SNCF:87611301",
        CONF_DEPARTURE_NAME: "Toulouse Matabiau (Toulouse)",
        CONF_ARRIVAL_NAME: "L'Isle-Jourdain (L'Isle-Jourdain)",
    }
    return coordinator


@pytest.mark.asyncio
async def test_unknown_station_is_searched_again_and_saved(hass):
    """'The entry point: <id> is not valid': the station id changed."""
    coordinator = _coordinator_with_names(hass)
    good = [{"id": "journey_1", "nb_transfers": 0}]
    rejected = SncfInvalidJourneyError(
        "unknown_object", "The entry point: stop_area:SNCF:87611301 is not valid"
    )
    coordinator.api_client.fetch_journeys = AsyncMock(
        side_effect=[rejected, good, good]
    )
    coordinator.api_client.search_stations = AsyncMock(
        return_value=[
            {"id": "stop_area:SNCF:87611999", "name": "L'Isle-Jourdain (Gers)"},
            {"id": "stop_area:OCE:SA:87611301", "name": "Autre nom"},
        ]
    )
    update = MagicMock()
    with (
        _now(),
        patch.object(hass.config_entries, "async_update_subentry", update),
        patch("custom_components.sncf_trains.coordinator.asyncio.sleep"),
    ):
        data = await coordinator._async_update_data()

    assert data == {"sub": good}
    # Only the rejected station is searched, by its short name.
    coordinator.api_client.search_stations.assert_awaited_once_with(
        "L'Isle-Jourdain"
    )
    # Same UIC code wins over a different station.
    retry = coordinator.api_client.fetch_journeys.await_args_list[1]
    assert retry.args[:2] == ("stop_area:SNCF:87611004", "stop_area:OCE:SA:87611301")
    saved = update.call_args.kwargs["data"]
    assert saved[CONF_TO] == "stop_area:OCE:SA:87611301"
    assert saved[CONF_FROM] == "stop_area:SNCF:87611004"
    assert ir.async_get(hass).async_get_issue(DOMAIN, "invalid_journey_sub") is None


@pytest.mark.asyncio
async def test_unknown_station_not_found_raises_repair_once(hass):
    coordinator = _coordinator_with_names(hass)
    rejected = SncfInvalidJourneyError(
        "unknown_object", "The entry point: stop_area:SNCF:87611301 is not valid"
    )
    coordinator.api_client.fetch_journeys = AsyncMock(side_effect=rejected)
    # Only the same, rejected id comes back: nothing better to try.
    coordinator.api_client.search_stations = AsyncMock(
        return_value=[{"id": "stop_area:SNCF:87611301", "name": "L'Isle-Jourdain"}]
    )
    with _now(), patch("custom_components.sncf_trains.coordinator.asyncio.sleep"):
        data = await coordinator._async_update_data()

    assert data == {"sub": []}
    assert coordinator.api_client.fetch_journeys.await_count == 1
    assert ir.async_get(hass).async_get_issue(DOMAIN, "invalid_journey_sub")
