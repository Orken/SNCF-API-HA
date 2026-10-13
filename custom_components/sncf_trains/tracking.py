"""Per-train status tracking and change detection.

The "Train N" sensors are positional (N-th next departure), so they shift
when a train leaves or a cancelled train is inserted. To let automations
react to a *specific* train, each train run gets a stable identifier
(``train_uid`` = train number + planned departure) and its last known status
is remembered. A change produces one event payload.

Pure Python: the coordinator handles persistence and firing the events.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .disruptions import (
    KEY_DESTINATION_DELETED,
    STATUS_ON_TIME,
    journey_cause,
    journey_key,
    journey_status,
    main_disruption,
    planned_departure,
)
from .helpers import parse_datetime

EVENT_STATUS_CHANGED = "sncf_trains_status_changed"

# How long a train is remembered after its planned departure.
RETENTION = timedelta(days=1)


def journey_uid(journey: dict[str, Any]) -> str | None:
    """Stable identifier of a train run: "<train number>_<YYYYmmddTHHMM>"."""
    key = journey_key(journey)
    return f"{key[0]}_{key[1]}" if key else None


def _pt_section(journey: dict[str, Any]) -> dict[str, Any]:
    for candidate in journey.get("sections") or []:
        if isinstance(candidate, dict) and candidate.get("type") == "public_transport":
            return candidate
    return {}


def _minutes(real: str, base: str) -> int:
    real_dt = parse_datetime(real or "")
    base_dt = parse_datetime(base or "")
    if real_dt and base_dt:
        return int((real_dt - base_dt).total_seconds() / 60)
    return 0


def journey_departure_delay(journey: dict[str, Any]) -> int:
    """Delay in minutes at departure (realtime - planned)."""
    return _minutes(
        journey.get("departure_date_time", ""),
        _pt_section(journey).get("base_departure_date_time", ""),
    )


def journey_arrival_delay(journey: dict[str, Any]) -> int:
    """Delay in minutes at arrival (realtime - planned)."""
    return _minutes(
        journey.get("arrival_date_time", ""),
        _pt_section(journey).get("base_arrival_date_time", ""),
    )


def journey_delay(journey: dict[str, Any]) -> int:
    """Delay retained for a train: the larger of departure and arrival delays."""
    return max(journey_departure_delay(journey), journey_arrival_delay(journey))


def detect_changes(
    previous: dict[str, dict[str, Any]],
    journeys_by_trip: dict[str, list[dict[str, Any]]],
    trip_info: dict[str, dict[str, Any]],
    now: datetime,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Compare current statuses with the previous ones.

    Returns ``(events, new_state)``:

    * an event is emitted when a train is first seen with a non on-time
      status, when its status changes, or when its delay changes while it
      is delayed;
    * ``new_state`` maps ``train_uid`` to its last status, keeping trains
      no longer returned by the API until ``RETENTION`` after departure.
    """
    events: list[dict[str, Any]] = []
    state: dict[str, dict[str, Any]] = {}

    # Keep recent entries that are not in the current answer (a train that
    # disappears briefly must not be re-announced when it comes back).
    for uid, entry in previous.items():
        dep = parse_datetime(entry.get("planned_departure", ""))
        if dep is None or dep + RETENTION >= now:
            state[uid] = entry

    for trip_id, journeys in journeys_by_trip.items():
        info = trip_info.get(trip_id, {})
        for journey in journeys:
            if not isinstance(journey, dict):
                continue
            uid = journey_uid(journey)
            dep = planned_departure(journey)
            if uid is None or dep is None:
                continue

            delay = journey_delay(journey)
            status = journey_status(journey, delay)
            served = not journey.get(KEY_DESTINATION_DELETED, False)
            before = previous.get(uid)

            changed = (
                (before is None and (status != STATUS_ON_TIME or not served))
                or (before is not None and before.get("status") != status)
                or (
                    before is not None
                    and before.get("destination_served", True) != served
                )
                or (
                    before is not None
                    and status != STATUS_ON_TIME
                    and before.get("delay_minutes") != delay
                )
            )

            disruption = main_disruption(journey) or {}
            record = {
                "trip_id": trip_id,
                "train_uid": uid,
                "train_num": uid.split("_", 1)[0],
                "planned_departure": dep.isoformat(),
                "status": status,
                "delay_minutes": delay,
                "departure_delay_minutes": journey_departure_delay(journey),
                "arrival_delay_minutes": journey_arrival_delay(journey),
                "destination_served": served,
            }
            state[uid] = record

            if changed:
                dep_rt = parse_datetime(journey.get("departure_date_time", ""))
                arr_rt = parse_datetime(journey.get("arrival_date_time", ""))
                events.append(
                    {
                        **record,
                        **info,
                        "previous_status": before.get("status") if before else None,
                        "departure_time": dep_rt.isoformat() if dep_rt else None,
                        "arrival_time": arr_rt.isoformat() if arr_rt else None,
                        "disruption_effect": disruption.get("effect"),
                        "disruption_message": journey_cause(journey),
                    }
                )

    return events, state
