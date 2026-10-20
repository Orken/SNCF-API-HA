"""Tests: station-only search (#6), device names (#8), departure delay (#5),
calendar train section (#10)."""

from unittest.mock import MagicMock

from custom_components.sncf_trains.const import (
    CONF_ARRIVAL_NAME,
    CONF_DEPARTURE_NAME,
    CONF_FROM,
    CONF_TO,
)
from custom_components.sncf_trains.disruptions import transport_section
from custom_components.sncf_trains.helpers import only_stations, short_station_name
from custom_components.sncf_trains.tracking import (
    journey_arrival_delay,
    journey_delay,
    journey_departure_delay,
)

# --- #6 ---------------------------------------------------------------------


def test_only_stations_keeps_stop_areas():
    places = [
        {"id": "admin:fr:31446", "embedded_type": "administrative_region"},
        {"id": "1.4697023;43.5529904", "embedded_type": "address"},
        {"id": "poi:osm:node:13188880376", "embedded_type": "poi"},
        {"id": "stop_area:SNCF:87611301", "embedded_type": "stop_area"},
        {"id": "stop_area:SNCF:87611004"},  # embedded_type missing
    ]

    assert [p["id"] for p in only_stations(places)] == [
        "stop_area:SNCF:87611301",
        "stop_area:SNCF:87611004",
    ]
    assert only_stations(None) == []


# --- #8 ---------------------------------------------------------------------


def test_short_station_name():
    assert short_station_name("L'Isle-Jourdain (L'Isle-Jourdain)") == "L'Isle-Jourdain"
    assert short_station_name("Saint-Agne (Toulouse)") == "Saint-Agne (Toulouse)"
    assert short_station_name("Toulouse Matabiau") == "Toulouse Matabiau"
    assert short_station_name("") == ""


def test_trip_device_named_after_stations():
    from custom_components.sncf_trains.sensor import trip_device_info

    subentry = MagicMock()
    subentry.subentry_id = "sub"
    subentry.data = {
        CONF_FROM: "stop_area:SNCF:87611806",
        CONF_TO: "stop_area:SNCF:87611301",
        CONF_DEPARTURE_NAME: "L'Isle-Jourdain (L'Isle-Jourdain)",
        CONF_ARRIVAL_NAME: "Saint-Agne (Toulouse)",
    }

    info = trip_device_info(subentry)

    assert info["name"] == "SNCF L'Isle-Jourdain → Saint-Agne (Toulouse)"
    assert info["identifiers"] == {("sncf_trains", "sub")}


# --- #5 ---------------------------------------------------------------------


def _journey(dep, base_dep, arr, base_arr):
    return {
        "departure_date_time": dep,
        "arrival_date_time": arr,
        "sections": [
            {"type": "crow_fly"},
            {
                "type": "public_transport",
                "base_departure_date_time": base_dep,
                "base_arrival_date_time": base_arr,
            },
        ],
    }


def test_departure_and_arrival_delays():
    # Leaves 8 min late, catches up to 3 min at arrival.
    journey = _journey(
        "20261014T075500", "20261014T074700", "20261014T083200", "20261014T082900"
    )

    assert journey_departure_delay(journey) == 8
    assert journey_arrival_delay(journey) == 3
    assert journey_delay(journey) == 8


def test_late_arrival_only():
    journey = _journey(
        "20261014T074700", "20261014T074700", "20261014T083900", "20261014T082900"
    )

    assert journey_departure_delay(journey) == 0
    assert journey_delay(journey) == 10


def _trip_coordinator(journey):
    subentry = MagicMock()
    subentry.subentry_id = "sub"
    subentry.data = {
        CONF_FROM: "stop_area:a",
        CONF_TO: "stop_area:b",
        CONF_DEPARTURE_NAME: "A",
        CONF_ARRIVAL_NAME: "B",
    }
    coordinator = MagicMock()
    coordinator.entry.entry_id = "entry"
    coordinator.entry.subentries = {"sub": subentry}
    coordinator.data = {"sub": [journey]}
    return coordinator


def test_line_sensor_and_calendar_report_departure_delay_like_train_sensor():
    """Leaves 10 min late, arrives on time: delayed by 10 min everywhere."""
    from custom_components.sncf_trains.calendar import SNCFCalendar
    from custom_components.sncf_trains.sensor import (
        SncfAllTrainsLineSensor,
        SncfTrainSensor,
    )

    journey = _journey(
        "20301014T075700", "20301014T074700", "20301014T082900", "20301014T082900"
    )
    coordinator = _trip_coordinator(journey)

    train = SncfTrainSensor(coordinator, "sub", 0).extra_state_attributes
    line = SncfAllTrainsLineSensor(coordinator, "sub").extra_state_attributes
    event = SNCFCalendar(coordinator).event

    assert (train["status"], train["delay_minutes"]) == ("delayed", 10)
    assert (line["status"], line["delay_minutes"], line["has_delay"]) == (
        "delayed",
        "10",
        True,
    )
    assert (event.has_delay, event.delay) == (True, 10)
    assert event.summary == "A → B - RETARD (10min)"


def test_train_sensor_names_its_stations():
    """The cards read the stations here, not in the (renamable) device name."""
    from custom_components.sncf_trains.sensor import SncfTrainSensor

    journey = _journey(
        "20301014T074700", "20301014T074700", "20301014T082900", "20301014T082900"
    )
    coordinator = _trip_coordinator(journey)
    coordinator.entry.subentries["sub"].data[CONF_DEPARTURE_NAME] = (
        "L'Isle-Jourdain (L'Isle-Jourdain)"
    )
    coordinator.entry.subentries["sub"].data[CONF_ARRIVAL_NAME] = (
        "Saint-Agne (Toulouse)"
    )

    train = SncfTrainSensor(coordinator, "sub", 0).extra_state_attributes

    # Mêmes noms que l'appareil « SNCF L'Isle-Jourdain → Saint-Agne (Toulouse) ».
    assert train["departure_name"] == "L'Isle-Jourdain"
    assert train["arrival_name"] == "Saint-Agne (Toulouse)"


# --- #10 --------------------------------------------------------------------


def test_transport_section_skips_walking():
    journey = {
        "sections": [
            {"type": "crow_fly", "id": "walk"},
            {"type": "public_transport", "id": "train"},
            {"type": "crow_fly", "id": "walk2"},
        ]
    }

    assert transport_section(journey)["id"] == "train"
