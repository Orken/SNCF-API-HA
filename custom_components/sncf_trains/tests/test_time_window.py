"""Tests for the time window filtering (end of window)."""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntry
from homeassistant.util import dt as dt_util

from custom_components.sncf_trains.const import CONF_API_KEY
from custom_components.sncf_trains.coordinator import SncfUpdateCoordinator


def _coordinator(hass) -> SncfUpdateCoordinator:
    entry = MagicMock(spec=ConfigEntry)
    entry.data = {CONF_API_KEY: "key"}
    entry.options = {}
    entry.subentries = {}
    return SncfUpdateCoordinator(hass, entry)


def _journey(base_dep: str) -> dict:
    return {
        "departure_date_time": base_dep,
        "sections": [
            {
                "type": "public_transport",
                "base_departure_date_time": base_dep,
                "display_informations": {"trip_short_name": base_dep[-6:]},
            }
        ],
    }


def _now(*args):
    return patch(
        "custom_components.sncf_trains.coordinator.dt_util.now",
        return_value=datetime(*args),
    )


@pytest.mark.parametrize(
    ("now", "start", "end", "expected"),
    [
        # Before today's window: window is today.
        ((2026, 10, 14, 6, 0), "07:30", "09:00", (2026, 10, 14, 9, 0)),
        # During the window: still today.
        ((2026, 10, 14, 8, 0), "07:30", "09:00", (2026, 10, 14, 9, 0)),
        # After the window: tomorrow's window.
        ((2026, 10, 14, 19, 0), "07:30", "09:00", (2026, 10, 15, 9, 0)),
        # Window crossing midnight ends the day after its start.
        ((2026, 10, 14, 20, 0), "22:00", "01:00", (2026, 10, 15, 1, 0)),
        # ...and is still the same window just after midnight.
        ((2026, 10, 15, 0, 30), "22:00", "01:00", (2026, 10, 15, 1, 0)),
        # ...and the next one once it is over.
        ((2026, 10, 15, 2, 0), "22:00", "01:00", (2026, 10, 16, 1, 0)),
    ],
)
def test_window_end(hass, now, start, end, expected):
    coordinator = _coordinator(hass)
    with _now(*now):
        window_end = coordinator._window_end(start, end)
    assert window_end.replace(tzinfo=None) == datetime(*expected)
    assert window_end.tzinfo is not None


def test_filter_by_window_drops_trains_after_end(hass):
    window_end = datetime(2026, 10, 14, 9, 0, tzinfo=dt_util.get_default_time_zone())
    journeys = [
        _journey("20261014T074700"),
        _journey("20261014T090000"),  # exactly at the end: kept
        _journey("20261014T094900"),
        _journey("20261014T154000"),
    ]

    kept = SncfUpdateCoordinator._filter_by_window(journeys, window_end)

    assert [j["departure_date_time"] for j in kept] == [
        "20261014T074700",
        "20261014T090000",
    ]


def test_filter_by_window_keeps_journeys_without_date(hass):
    window_end = datetime(2026, 10, 14, 9, 0, tzinfo=dt_util.get_default_time_zone())
    journeys = [{"id": "no_date"}]

    assert SncfUpdateCoordinator._filter_by_window(journeys, window_end) == journeys
