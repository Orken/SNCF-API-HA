"""Tests for the optional end time: the train count limits the window."""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sncf_trains.const import (
    CONF_API_KEY,
    CONF_ARRIVAL_CITY,
    CONF_ARRIVAL_STATION,
    CONF_DEPARTURE_CITY,
    CONF_DEPARTURE_STATION,
    CONF_FROM,
    CONF_TIME_END,
    CONF_TIME_START,
    CONF_TO,
    CONF_TRAIN_COUNT,
    DOMAIN,
)
from custom_components.sncf_trains.coordinator import SncfUpdateCoordinator
from custom_components.sncf_trains.helpers import time_range_id, trip_title

# ---------------------------------------------------------------------------
# Titles and unique ids
# ---------------------------------------------------------------------------


def test_trip_title_with_end_time():
    data = {CONF_TIME_START: "07:00", CONF_TIME_END: "10:00", CONF_TRAIN_COUNT: 3}
    assert trip_title("A", "B", data) == "Trajet: A → B (07:00 - 10:00)"
    assert time_range_id(data) == "07:00_10:00"


def test_trip_title_without_end_time():
    data = {CONF_TIME_START: "07:00", CONF_TRAIN_COUNT: 3}
    assert trip_title("A", "B", data) == "Trajet: A → B (07:00, 3 trains)"
    assert time_range_id(data) == "07:00_x3"
    assert trip_title("A", "B", {**data, CONF_TRAIN_COUNT: 1}).endswith(
        "(07:00, 1 train)"
    )


# ---------------------------------------------------------------------------
# Config flow
# ---------------------------------------------------------------------------


def _main_entry(hass, subentries_data=()) -> MockConfigEntry:
    entry = MockConfigEntry(
        version=1,
        minor_version=2,
        domain=DOMAIN,
        title="Trains SNCF",
        data={CONF_API_KEY: "valid_key"},
        source=config_entries.SOURCE_USER,
        unique_id="sncf_trains",
        subentries_data=subentries_data,
    )
    entry.add_to_hass(hass)
    return entry


async def _reach_time_range(hass, entry):
    """Walk the subentry flow up to the time range step."""
    mock_api = AsyncMock()
    mock_api.search_stations = AsyncMock(
        side_effect=[
            [{"id": "stop_area:dep", "name": "Paris Gare de Lyon"}],
            [{"id": "stop_area:arr", "name": "Lyon Part Dieu"}],
        ]
    )
    with patch(
        "custom_components.sncf_trains.config_flow.SncfApiClient",
        return_value=mock_api,
    ):
        result = await hass.config_entries.subentries.async_init(
            (entry.entry_id, "train"),
            context={"source": config_entries.SOURCE_USER},
        )
        for user_input in (
            {CONF_DEPARTURE_CITY: "Paris"},
            {CONF_DEPARTURE_STATION: "stop_area:dep"},
            {CONF_ARRIVAL_CITY: "Lyon"},
            {CONF_ARRIVAL_STATION: "stop_area:arr"},
        ):
            result = await hass.config_entries.subentries.async_configure(
                result["flow_id"], user_input=user_input
            )
    assert result["step_id"] == "time_range"
    return result


@pytest.mark.asyncio
async def test_subentry_without_end_time(hass):
    entry = _main_entry(hass)
    result = await _reach_time_range(hass, entry)

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={CONF_TIME_START: "06:00", CONF_TRAIN_COUNT: 2},
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == (
        "Trajet: Paris Gare de Lyon → Lyon Part Dieu (06:00, 2 trains)"
    )
    assert CONF_TIME_END not in result["data"]
    assert result["data"][CONF_TRAIN_COUNT] == 2
    assert result["unique_id"] == "stop_area:dep_stop_area:arr_06:00_x2"


@pytest.mark.asyncio
async def test_subentry_rejects_invalid_time(hass):
    entry = _main_entry(hass)
    result = await _reach_time_range(hass, entry)

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={
            CONF_TIME_START: "06:00",
            CONF_TIME_END: "25:00",
            CONF_TRAIN_COUNT: 2,
        },
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_TIME_END: "invalid_time"}


@pytest.mark.asyncio
async def test_subentry_rejects_out_of_range_train_count(hass):
    entry = _main_entry(hass)
    result = await _reach_time_range(hass, entry)

    with pytest.raises(vol.Invalid):
        await hass.config_entries.subentries.async_configure(
            result["flow_id"],
            user_input={CONF_TIME_START: "06:00", CONF_TRAIN_COUNT: 0},
        )


@pytest.mark.asyncio
async def test_reconfigure_clears_end_time(hass):
    entry = _main_entry(
        hass,
        (
            {
                "data": {
                    "from": "stop_area:dep",
                    "to": "stop_area:arr",
                    "departure_name": "Paris Gare de Lyon",
                    "arrival_name": "Lyon Part Dieu",
                    "time_start": "07:00",
                    "time_end": "10:00",
                    "train_count": 5,
                },
                "subentry_type": "train",
                "title": "Existing train",
                "unique_id": "stop_area:dep_stop_area:arr_07:00_10:00",
            },
        ),
    )
    subentry_id = next(iter(entry.subentries))

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "train"),
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "subentry_id": subentry_id,
        },
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        user_input={CONF_TIME_START: "07:00", CONF_TRAIN_COUNT: 3},
    )

    assert result["type"] is FlowResultType.ABORT
    subentry = entry.subentries[subentry_id]
    assert CONF_TIME_END not in subentry.data
    assert subentry.data[CONF_TRAIN_COUNT] == 3
    assert subentry.title == (
        "Trajet: Paris Gare de Lyon → Lyon Part Dieu (07:00, 3 trains)"
    )


# ---------------------------------------------------------------------------
# Coordinator: end of window
# ---------------------------------------------------------------------------


def _coordinator(hass, **data) -> SncfUpdateCoordinator:
    subentry = MagicMock()
    subentry.title = "Trajet"
    subentry.data = {CONF_FROM: "stop_area:dep", CONF_TO: "stop_area:arr", **data}
    entry = MagicMock(spec=ConfigEntry)
    entry.data = {CONF_API_KEY: "key"}
    entry.options = {}
    entry.subentries = {"trip": subentry}
    return SncfUpdateCoordinator(hass, entry)


def _journey(dep: datetime) -> dict:
    value = dep.strftime("%Y%m%dT%H%M%S")
    return {
        "nb_transfers": 0,
        "departure_date_time": value,
        "sections": [
            {
                "type": "public_transport",
                "base_departure_date_time": value,
                "display_informations": {"trip_short_name": value[-6:-2]},
            }
        ],
    }


def _api() -> AsyncMock:
    """Trains 10, 40 and 70 minutes after the requested start."""

    async def fetch(_from, _to, datetime_str, count=5, data_freshness="realtime"):
        if data_freshness != "realtime":
            return []
        start = datetime.strptime(datetime_str, "%Y%m%dT%H%M%S")
        return [_journey(start + timedelta(minutes=m)) for m in (10, 40, 70)]

    api = AsyncMock()
    api.fetch_journeys = AsyncMock(side_effect=fetch)
    return api


def _requested(api: AsyncMock) -> list[str]:
    return [
        call.args[2]
        for call in api.fetch_journeys.await_args_list
        if call.kwargs.get("data_freshness", "realtime") == "realtime"
    ]


def _departures(data: dict) -> list[str]:
    return [j["departure_date_time"] for j in data["trip"]]


async def _update(coordinator, *now):
    with patch(
        "custom_components.sncf_trains.coordinator.dt_util.now",
        return_value=datetime(*now),
    ):
        return await coordinator._async_update_data()


@pytest.mark.asyncio
async def test_count_only_shows_count_trains(hass):
    coordinator = _coordinator(hass, time_start="06:00", train_count=2)
    coordinator.api_client = _api()

    data = await _update(coordinator, 2026, 10, 14, 6, 30)

    assert _requested(coordinator.api_client) == ["20261014T060000"]
    assert _departures(data) == ["20261014T061000", "20261014T064000"]
    start, end = coordinator._windows["trip"]
    assert end.replace(tzinfo=None) == datetime(2026, 10, 14, 6, 40)
    assert coordinator.update_interval == timedelta(minutes=2)


@pytest.mark.asyncio
async def test_count_only_moves_to_next_day_once_last_train_left(hass):
    coordinator = _coordinator(hass, time_start="06:00", train_count=2)
    coordinator.api_client = _api()

    data = await _update(coordinator, 2026, 10, 14, 7, 0)

    assert _requested(coordinator.api_client) == [
        "20261014T060000",
        "20261015T060000",
    ]
    assert _departures(data) == ["20261015T061000", "20261015T064000"]
    assert coordinator.update_interval == timedelta(minutes=60)

    # The window is now known: a single call for the next update.
    coordinator.api_client = _api()
    await _update(coordinator, 2026, 10, 14, 8, 0)
    assert _requested(coordinator.api_client) == ["20261015T060000"]


@pytest.mark.asyncio
async def test_count_only_before_start_checks_previous_window(hass):
    coordinator = _coordinator(hass, time_start="06:00", train_count=2)
    coordinator.api_client = _api()

    data = await _update(coordinator, 2026, 10, 14, 5, 0)

    # Yesterday's window could still be running (late trains); it is not.
    assert _requested(coordinator.api_client) == [
        "20261013T060000",
        "20261014T060000",
    ]
    assert _departures(data) == ["20261014T061000", "20261014T064000"]


@pytest.mark.asyncio
async def test_end_time_and_count_first_limit_wins(hass):
    # Count reached first: the window ends with the 2nd train (06:40).
    coordinator = _coordinator(
        hass, time_start="06:00", time_end="12:00", train_count=2
    )
    coordinator.api_client = _api()

    data = await _update(coordinator, 2026, 10, 14, 7, 0)

    assert _requested(coordinator.api_client) == [
        "20261014T060000",
        "20261015T060000",
    ]
    assert _departures(data) == ["20261015T061000", "20261015T064000"]


@pytest.mark.asyncio
async def test_end_time_and_count_end_time_wins(hass):
    # End time reached first: only the 06:10 train, until 06:30.
    coordinator = _coordinator(
        hass, time_start="06:00", time_end="06:30", train_count=5
    )
    coordinator.api_client = _api()

    data = await _update(coordinator, 2026, 10, 14, 6, 20)

    assert _requested(coordinator.api_client) == ["20261014T060000"]
    assert _departures(data) == ["20261014T061000"]
    start, end = coordinator._windows["trip"]
    assert end.replace(tzinfo=None) == datetime(2026, 10, 14, 6, 30)
