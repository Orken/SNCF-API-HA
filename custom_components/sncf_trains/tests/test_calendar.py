"""Calendar state: the next train, in Home Assistant's time zone."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock

from homeassistant.util import dt as dt_util

from custom_components.sncf_trains.calendar import SNCFCalendar
from custom_components.sncf_trains.const import (
    CONF_ARRIVAL_NAME,
    CONF_DEPARTURE_NAME,
    CONF_FROM,
    CONF_TO,
)


def _journey(dep: str) -> dict:
    arrival = (
        datetime.strptime(dep, "%Y%m%dT%H%M%S") + timedelta(minutes=40)
    ).strftime("%Y%m%dT%H%M%S")
    return {
        "departure_date_time": dep,
        "arrival_date_time": arrival,
        "sections": [
            {
                "type": "public_transport",
                "id": f"section_{dep}",
                "base_departure_date_time": dep,
                "base_arrival_date_time": arrival,
                "display_informations": {"trip_short_name": dep[-6:-2]},
            }
        ],
    }


def _calendar(*departures: str) -> SNCFCalendar:
    subentry = MagicMock()
    subentry.data = {
        CONF_FROM: "stop_area:a",
        CONF_TO: "stop_area:b",
        CONF_DEPARTURE_NAME: "A",
        CONF_ARRIVAL_NAME: "B",
    }
    coordinator = MagicMock()
    coordinator.entry.entry_id = "entry"
    coordinator.entry.subentries = {"sub": subentry}
    coordinator.data = {"sub": [_journey(dep) for dep in departures]}
    return SNCFCalendar(coordinator)


async def test_calendar_shows_next_train_not_the_closest_departed_one(hass, freezer):
    # System clock in UTC, Home Assistant in Paris: 07:50 local time.
    await hass.config.async_set_time_zone("Europe/Paris")
    freezer.move_to("2026-10-14T05:50:00+00:00")

    calendar = _calendar("20261014T070000", "20261014T073000", "20261014T083000")

    paris = dt_util.get_time_zone("Europe/Paris")
    assert calendar.event.start == datetime(2026, 10, 14, 8, 30, tzinfo=paris)


async def test_calendar_has_no_event_once_every_train_has_left(hass, freezer):
    await hass.config.async_set_time_zone("Europe/Paris")
    freezer.move_to("2026-10-14T07:00:00+00:00")  # 09:00 in Paris

    calendar = _calendar("20261014T070000", "20261014T073000", "20261014T083000")

    assert calendar.event is None
    assert calendar.extra_state_attributes == {}
