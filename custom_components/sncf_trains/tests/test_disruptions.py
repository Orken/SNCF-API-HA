"""Tests for cancelled-train detection and disruption attachment."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntry

from custom_components.sncf_trains.const import (
    CONF_API_KEY,
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
    STATUS_DELAYED,
    STATUS_ON_TIME,
    attach_disruptions,
    journey_status,
    main_disruption,
    merge_with_schedule,
)


def _journey(
    num: str,
    base_dep: str,
    dep: str | None = None,
    links: list | None = None,
    status: str = "",
) -> dict:
    """Build a Navitia-like direct journey (crow_fly / PT / crow_fly)."""
    dep = dep or base_dep
    return {
        "nb_transfers": 0,
        "status": status,
        "departure_date_time": dep,
        "arrival_date_time": dep,
        "sections": [
            {"type": "crow_fly"},
            {
                "type": "public_transport",
                "id": f"section_{num}",
                "base_departure_date_time": base_dep,
                "departure_date_time": dep,
                "base_arrival_date_time": base_dep,
                "display_informations": {
                    "trip_short_name": num,
                    "links": links or [],
                },
            },
            {"type": "crow_fly"},
        ],
    }


def _disruption(dis_id: str, effect: str, train: str, text: str = "") -> dict:
    return {
        "id": dis_id,
        "severity": {"effect": effect},
        "messages": [{"text": text}] if text else [],
        "impacted_objects": [
            {
                "pt_object": {
                    "embedded_type": "trip",
                    "name": train,
                    "trip": {"id": f"trip:{train}", "name": train},
                },
                "impacted_stops": [{"cause": "Panne de signalisation"}],
            }
        ],
    }


# ---------------------------------------------------------------------------
# merge_with_schedule
# ---------------------------------------------------------------------------


def test_merge_flags_missing_planned_train_as_cancelled():
    realtime = [
        _journey("870302", "20261014T074700"),
        _journey("870306", "20261014T094900"),
    ]
    base = [
        _journey("870302", "20261014T074700"),
        _journey("870304", "20261014T084700"),
        _journey("870306", "20261014T094900"),
    ]

    merged = merge_with_schedule(realtime, base)

    assert [
        j["sections"][1]["display_informations"]["trip_short_name"] for j in merged
    ] == [
        "870302",
        "870304",
        "870306",
    ]
    assert merged[1][KEY_STATUS] == STATUS_CANCELLED
    assert KEY_STATUS not in merged[0]
    assert KEY_STATUS not in merged[2]


def test_merge_ignores_planned_trains_after_last_realtime_train():
    """A planned train later than the realtime results is just beyond `count`."""
    realtime = [_journey("870302", "20261014T074700")]
    base = [
        _journey("870302", "20261014T074700"),
        _journey("870304", "20261014T084700"),
    ]

    merged = merge_with_schedule(realtime, base)

    assert len(merged) == 1


def test_merge_keeps_later_train_when_disruption_confirms_cancellation():
    realtime = [_journey("870302", "20261014T074700")]
    late = _journey("870304", "20261014T084700")
    late[KEY_DISRUPTIONS] = [{"effect": "NO_SERVICE", "message": "Supprimé"}]

    merged = merge_with_schedule(realtime, [late])

    assert len(merged) == 2
    assert merged[1][KEY_STATUS] == STATUS_CANCELLED


def test_merge_same_number_on_another_day_is_not_confused():
    realtime = [_journey("870302", "20261015T074700")]
    base = [_journey("870302", "20261015T074700")]

    assert len(merge_with_schedule(realtime, base)) == 1


def test_merge_without_base_schedule_returns_realtime():
    realtime = [_journey("870302", "20261014T074700")]

    assert merge_with_schedule(realtime, None) == realtime


def test_merge_realtime_journey_with_no_service_status():
    realtime = [_journey("870302", "20261014T074700", status="NO_SERVICE")]

    merged = merge_with_schedule(realtime, None)

    assert merged[0][KEY_STATUS] == STATUS_CANCELLED


def test_merge_does_not_mutate_base_schedule():
    realtime = [_journey("870306", "20261014T094900")]
    planned = _journey("870304", "20261014T084700")

    merge_with_schedule(realtime, [planned])

    assert KEY_STATUS not in planned


# ---------------------------------------------------------------------------
# Disruptions
# ---------------------------------------------------------------------------


def test_attach_disruptions_by_link_and_by_train_number():
    linked = _journey(
        "870302",
        "20261014T074700",
        links=[{"type": "disruption", "id": "dis-1"}],
    )
    by_number = _journey("870304", "20261014T084700")
    untouched = _journey("870306", "20261014T094900")

    attach_disruptions(
        [linked, by_number, untouched],
        [
            _disruption("dis-1", "SIGNIFICANT_DELAYS", "999999", "Retard de 20 min"),
            _disruption("dis-2", "NO_SERVICE", "870304", "Train supprimé"),
        ],
    )

    assert linked[KEY_DISRUPTIONS] == [
        {
            "id": "dis-1",
            "effect": "SIGNIFICANT_DELAYS",
            "message": "Retard de 20 min",
            "cause": "Panne de signalisation",
            "deleted_stops": [],
        }
    ]
    assert by_number[KEY_DISRUPTIONS][0]["effect"] == "NO_SERVICE"
    assert untouched[KEY_DISRUPTIONS] == []


def test_disruption_message_falls_back_to_cause():
    journey = _journey("870304", "20261014T084700")

    attach_disruptions([journey], [_disruption("d", "NO_SERVICE", "870304")])

    assert journey[KEY_DISRUPTIONS][0]["message"] == "Panne de signalisation"


def test_main_disruption_picks_most_severe():
    journey = {
        KEY_DISRUPTIONS: [
            {"effect": "SIGNIFICANT_DELAYS"},
            {"effect": "NO_SERVICE"},
            {"effect": "OTHER_EFFECT"},
        ]
    }

    assert main_disruption(journey)["effect"] == "NO_SERVICE"
    assert main_disruption({}) is None


def test_journey_status():
    assert journey_status({}, 0) == STATUS_ON_TIME
    assert journey_status({}, 7) == STATUS_DELAYED
    assert journey_status({KEY_STATUS: STATUS_CANCELLED}, 0) == STATUS_CANCELLED


# ---------------------------------------------------------------------------
# Coordinator integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_coordinator_reports_cancelled_train(hass):
    subentry = MagicMock()
    subentry.title = "L'Isle-Jourdain → Saint-Agne"
    subentry.data = {
        CONF_FROM: "stop_area:SNCF:87611806",
        CONF_TO: "stop_area:SNCF:87611301",
        CONF_TIME_START: "07:30",
        CONF_TIME_END: "09:00",
    }
    entry = MagicMock(spec=ConfigEntry)
    entry.data = {CONF_API_KEY: "key"}
    entry.options = {}
    entry.subentries = {"sub": subentry}

    realtime = [
        _journey("870302", "20261014T074700"),
        _journey("870306", "20261014T094900"),
    ]
    planned = [
        _journey("870302", "20261014T074700"),
        _journey("870304", "20261014T084700"),
        _journey("870306", "20261014T094900"),
    ]

    async def fake_fetch(*args, data_freshness="realtime", **kwargs):
        return planned if data_freshness == "base_schedule" else realtime

    coordinator = SncfUpdateCoordinator(hass, entry)
    coordinator.api_client = AsyncMock()
    coordinator.api_client.fetch_journeys = AsyncMock(side_effect=fake_fetch)

    with patch(
        "custom_components.sncf_trains.coordinator.dt_util.now",
        return_value=datetime(2026, 10, 14, 7, 0),
    ):
        data = await coordinator._async_update_data()

    # 09:49 is planned after the 09:00 end of the window: filtered out.
    statuses = [j.get(KEY_STATUS) for j in data["sub"]]
    assert statuses == [None, STATUS_CANCELLED]


@pytest.mark.asyncio
async def test_coordinator_survives_base_schedule_failure(hass):
    subentry = MagicMock()
    subentry.title = "t"
    subentry.data = {
        CONF_FROM: "stop_area:a",
        CONF_TO: "stop_area:b",
        CONF_TIME_START: "07:00",
        CONF_TIME_END: "10:00",
    }
    entry = MagicMock(spec=ConfigEntry)
    entry.data = {CONF_API_KEY: "key"}
    entry.options = {}
    entry.subentries = {"sub": subentry}

    realtime = [_journey("870302", "20261014T074700")]

    async def fake_fetch(*args, data_freshness="realtime", **kwargs):
        if data_freshness == "base_schedule":
            raise RuntimeError("Quota exceeded: 429 Too Many Requests.")
        return realtime

    coordinator = SncfUpdateCoordinator(hass, entry)
    coordinator.api_client = AsyncMock()
    coordinator.api_client.fetch_journeys = AsyncMock(side_effect=fake_fetch)

    with patch(
        "custom_components.sncf_trains.coordinator.dt_util.now",
        return_value=datetime(2026, 10, 14, 7, 0),
    ):
        data = await coordinator._async_update_data()

    assert data == {"sub": realtime}
