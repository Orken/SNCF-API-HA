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
