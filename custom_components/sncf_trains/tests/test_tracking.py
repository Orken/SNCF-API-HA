"""Tests for per-train status tracking and status-change events."""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntry
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_capture_events

from custom_components.sncf_trains.const import (
    CONF_API_KEY,
    CONF_ARRIVAL_NAME,
    CONF_DEPARTURE_NAME,
    CONF_FROM,
    CONF_TIME_END,
    CONF_TIME_START,
    CONF_TO,
)
from custom_components.sncf_trains.coordinator import SncfUpdateCoordinator
from custom_components.sncf_trains.disruptions import (
    KEY_DISRUPTIONS,
    KEY_STATUS,
    STATUS_CANCELLED,
)
from custom_components.sncf_trains.tracking import (
    EVENT_STATUS_CHANGED,
    detect_changes,
    journey_delay,
    journey_uid,
)


def _journey(num: str, base_dep: str, delay: int = 0, cancelled: bool = False) -> dict:
    base = datetime.strptime(base_dep, "%Y%m%dT%H%M%S")
    base_arr = base + timedelta(minutes=42)
    arr = base_arr + timedelta(minutes=delay)
    journey = {
        "departure_date_time": (base + timedelta(minutes=delay)).strftime(
            "%Y%m%dT%H%M%S"
        ),
        "arrival_date_time": arr.strftime("%Y%m%dT%H%M%S"),
        "sections": [
            {
                "type": "public_transport",
                "base_departure_date_time": base_dep,
                "base_arrival_date_time": base_arr.strftime("%Y%m%dT%H%M%S"),
                "display_informations": {"trip_short_name": num},
            }
        ],
    }
    if cancelled:
        journey[KEY_STATUS] = STATUS_CANCELLED
        journey[KEY_DISRUPTIONS] = [
            {"effect": "NO_SERVICE", "message": "Indisponibilité d'un matériel"}
        ]
    return journey


NOW = datetime(2026, 10, 13, 20, 0, tzinfo=dt_util.get_default_time_zone())
INFO = {"trip": {"trip_title": "Aller"}}


def test_uid_and_delay():
    journey = _journey("870302", "20261014T074700", delay=7)

    assert journey_uid(journey) == "870302_20261014T0747"
    assert journey_delay(journey) == 7


def test_first_sighting_on_time_is_silent():
    events, state = detect_changes(
        {}, {"trip": [_journey("870302", "20261014T074700")]}, INFO, NOW
    )

    assert events == []
    assert state["870302_20261014T0747"]["status"] == "on_time"


def test_first_sighting_cancelled_emits_event_with_details():
    events, _ = detect_changes(
        {},
        {"trip": [_journey("870404", "20261014T081600", cancelled=True)]},
        INFO,
        NOW,
    )

    assert len(events) == 1
    event = events[0]
    assert event["train_uid"] == "870404_20261014T0816"
    assert event["train_num"] == "870404"
    assert event["status"] == "cancelled"
    assert event["previous_status"] is None
    assert event["disruption_message"] == "Indisponibilité d'un matériel"
    assert event["trip_title"] == "Aller"


def test_unchanged_status_is_silent_and_changes_are_reported():
    journeys = {"trip": [_journey("870302", "20261014T074700")]}
    _, state = detect_changes({}, journeys, INFO, NOW)

    # Same data again: nothing.
    events, state = detect_changes(state, journeys, INFO, NOW)
    assert events == []

    # Becomes delayed by 5 min.
    events, state = detect_changes(
        state, {"trip": [_journey("870302", "20261014T074700", delay=5)]}, INFO, NOW
    )
    assert [(e["status"], e["delay_minutes"]) for e in events] == [("delayed", 5)]

    # Delay grows: reported again.
    events, state = detect_changes(
        state, {"trip": [_journey("870302", "20261014T074700", delay=12)]}, INFO, NOW
    )
    assert [(e["previous_status"], e["delay_minutes"]) for e in events] == [
        ("delayed", 12)
    ]

    # Back on time: reported.
    events, state = detect_changes(state, journeys, INFO, NOW)
    assert [(e["previous_status"], e["status"]) for e in events] == [
        ("delayed", "on_time")
    ]


def test_slot_shift_does_not_re_announce():
    """Positions change but each train keeps its own state."""
    cancelled = _journey("870404", "20261014T081600", cancelled=True)
    first = [_journey("870302", "20261014T074700"), cancelled]
    _, state = detect_changes({}, {"trip": first}, INFO, NOW)

    # The 07:47 has left: the cancelled train moves to position 1.
    events, _ = detect_changes(state, {"trip": [cancelled]}, INFO, NOW)

    assert events == []


def test_old_trains_are_forgotten_recent_missing_ones_are_kept():
    previous = {
        "old_20261010T0747": {"planned_departure": "2026-10-10T07:47:00+02:00"},
        "recent_20261013T1900": {"planned_departure": "2026-10-13T19:00:00+02:00"},
    }

    _, state = detect_changes(previous, {}, INFO, NOW)

    assert set(state) == {"recent_20261013T1900"}


@pytest.mark.asyncio
async def test_coordinator_fires_event_once(hass):
    subentry = MagicMock()
    subentry.title = "L'Isle-Jourdain → Saint-Agne"
    subentry.data = {
        CONF_FROM: "stop_area:a",
        CONF_TO: "stop_area:b",
        CONF_TIME_START: "07:30",
        CONF_TIME_END: "09:00",
        CONF_DEPARTURE_NAME: "L'Isle-Jourdain",
        CONF_ARRIVAL_NAME: "Saint-Agne",
    }
    entry = MagicMock(spec=ConfigEntry)
    entry.data = {CONF_API_KEY: "key"}
    entry.options = {}
    entry.subentries = {"sub": subentry}

    realtime = [
        _journey("870302", "20261014T074700"),
        _journey("870406", "20261014T084700"),
    ]
    planned = realtime + [_journey("870404", "20261014T081600")]

    async def fake_fetch(*args, data_freshness="realtime", **kwargs):
        return planned if data_freshness == "base_schedule" else list(realtime)

    coordinator = SncfUpdateCoordinator(hass, entry)
    coordinator.api_client = AsyncMock()
    coordinator.api_client.fetch_journeys = AsyncMock(side_effect=fake_fetch)
    events = async_capture_events(hass, EVENT_STATUS_CHANGED)

    with patch(
        "custom_components.sncf_trains.coordinator.dt_util.now",
        return_value=datetime(2026, 10, 13, 20, 0),
    ):
        await coordinator._async_update_data()
        await coordinator._async_update_data()
        await hass.async_block_till_done()

    assert len(events) == 1
    data = events[0].data
    assert data["train_uid"] == "870404_20261014T0816"
    assert data["status"] == "cancelled"
    assert data["departure_name"] == "L'Isle-Jourdain"
    assert data["arrival_name"] == "Saint-Agne"


def test_train_sensor_created_for_empty_slot(hass):
    """A slot with no train yet is created empty instead of crashing."""
    from custom_components.sncf_trains.sensor import SncfTrainSensor

    subentry = MagicMock()
    subentry.subentry_id = "sub"
    subentry.data = {
        CONF_FROM: "stop_area:a",
        CONF_TO: "stop_area:b",
        CONF_DEPARTURE_NAME: "A",
        CONF_ARRIVAL_NAME: "B",
    }
    coordinator = MagicMock()
    coordinator.entry.subentries = {"sub": subentry}
    coordinator.data = {"sub": [_journey("870302", "20261014T074700")]}

    filled = SncfTrainSensor(coordinator, "sub", 0)
    empty = SncfTrainSensor(coordinator, "sub", 3)

    assert filled.native_value is not None
    assert filled.extra_state_attributes["train_uid"] == "870302_20261014T0747"
    assert empty.native_value is None
    assert empty.extra_state_attributes == {}
