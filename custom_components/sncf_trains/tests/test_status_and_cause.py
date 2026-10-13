"""Tests for departed status, deleted destination stop and cause lookup."""

from datetime import datetime

from homeassistant.util import dt as dt_util

from custom_components.sncf_trains.disruptions import (
    KEY_DESTINATION_DELETED,
    KEY_DISRUPTIONS,
    KEY_STATUS,
    STATUS_CANCELLED,
    STATUS_DELAYED,
    STATUS_DEPARTED,
    STATUS_ON_TIME,
    attach_disruptions,
    journey_cause,
    journey_status,
    mark_destination_deleted,
)
from custom_components.sncf_trains.tracking import detect_changes


def _journey(num="870302", dep="20261014T074700", **section_extra) -> dict:
    return {
        "departure_date_time": dep,
        "arrival_date_time": dep,
        "sections": [
            {
                "type": "public_transport",
                "base_departure_date_time": dep,
                "base_arrival_date_time": dep,
                "to": {"name": "Saint-Agne"},
                "display_informations": {"trip_short_name": num},
                **section_extra,
            }
        ],
    }


def _at(*args) -> datetime:
    return datetime(*args, tzinfo=dt_util.get_default_time_zone())


# --- departed ---------------------------------------------------------------


def test_departed_status_only_with_now():
    journey = _journey()

    assert journey_status(journey, 0) == STATUS_ON_TIME
    assert journey_status(journey, 0, _at(2026, 10, 14, 7, 0)) == STATUS_ON_TIME
    assert journey_status(journey, 0, _at(2026, 10, 14, 7, 47)) == STATUS_DEPARTED
    assert journey_status(journey, 3, _at(2026, 10, 14, 8, 0)) == STATUS_DEPARTED
    assert journey_status(journey, 3, _at(2026, 10, 14, 7, 0)) == STATUS_DELAYED


def test_departed_uses_realtime_departure():
    journey = _journey()
    journey["departure_date_time"] = "20261014T075700"  # 10 min late

    assert journey_status(journey, 10, _at(2026, 10, 14, 7, 50)) == STATUS_DELAYED
    assert journey_status(journey, 10, _at(2026, 10, 14, 7, 57)) == STATUS_DEPARTED


def test_cancelled_wins_over_departed():
    journey = _journey()
    journey[KEY_STATUS] = STATUS_CANCELLED

    assert journey_status(journey, 0, _at(2026, 10, 14, 9, 0)) == STATUS_CANCELLED


def test_departure_does_not_emit_tracking_event():
    journeys = {"t": [_journey()]}
    _, state = detect_changes({}, journeys, {}, _at(2026, 10, 14, 7, 0))

    # Tracking ignores "departed": nothing changes after the departure time.
    events, _ = detect_changes(state, journeys, {}, _at(2026, 10, 14, 8, 0))

    assert events == []


# --- deleted destination stop ---------------------------------------------


def _disruption_deleting(stop_id: str, train="870302") -> dict:
    return {
        "id": "d1",
        "severity": {"effect": "REDUCED_SERVICE"},
        "messages": [],
        "impacted_objects": [
            {
                "pt_object": {"name": train, "trip": {"name": train}},
                "impacted_stops": [
                    {
                        "stop_point": {"id": stop_id, "name": "Saint-Agne"},
                        "stop_time_effect": "deleted",
                        "departure_status": "deleted",
                        "arrival_status": "deleted",
                    }
                ],
            }
        ],
    }


def test_deleted_destination_stop_cancels_the_train_for_the_user():
    journey = _journey()
    attach_disruptions(
        [journey], [_disruption_deleting("stop_point:SNCF:87611301:Train")]
    )

    mark_destination_deleted([journey], "stop_area:SNCF:87611301")

    assert journey[KEY_DESTINATION_DELETED] is True
    assert journey[KEY_STATUS] == STATUS_CANCELLED
    assert journey_cause(journey) == "Arrêt Saint-Agne supprimé"


def test_other_deleted_stop_is_ignored():
    journey = _journey()
    attach_disruptions(
        [journey], [_disruption_deleting("stop_point:SNCF:87611467:Train")]
    )

    mark_destination_deleted([journey], "stop_area:SNCF:87611301")

    assert KEY_DESTINATION_DELETED not in journey
    assert KEY_STATUS not in journey


# --- cause ------------------------------------------------------------------


def test_cause_priority():
    journey = _journey(cause="Panne de signalisation")
    journey["messages"] = [{"text": "Message du trajet"}]

    # Section cause before journey messages.
    assert journey_cause(journey) == "Panne de signalisation"

    # Disruption message first.
    journey[KEY_DISRUPTIONS] = [{"effect": "SIGNIFICANT_DELAYS", "message": "Grève"}]
    assert journey_cause(journey) == "Grève"

    # Journey messages when nothing else.
    bare = _journey()
    bare["messages"] = [{"text": "Message du trajet"}]
    assert journey_cause(bare) == "Message du trajet"

    assert journey_cause(_journey()) is None
