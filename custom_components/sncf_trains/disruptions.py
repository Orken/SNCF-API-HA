"""Detection of cancelled trains and SNCF disruption details.

The SNCF (Navitia) journey planner drops cancelled trains from
``data_freshness=realtime`` results instead of flagging them. To surface
them we also query the planned timetable (``data_freshness=base_schedule``)
and compare both lists: a planned train that is missing from the realtime
answer is reported as cancelled.

Everything here is pure Python so it can be unit-tested without Home
Assistant.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from .helpers import get_train_num, parse_datetime

# Keys added to journey dicts by this integration (not part of the API).
KEY_STATUS = "sncf_status"
KEY_DISRUPTIONS = "sncf_disruptions"
KEY_DESTINATION_DELETED = "sncf_destination_deleted"

STATUS_ON_TIME = "on_time"
STATUS_DELAYED = "delayed"
STATUS_DEPARTED = "departed"
STATUS_CANCELLED = "cancelled"

EFFECT_NO_SERVICE = "NO_SERVICE"

# Most severe first, used to pick the disruption shown to the user.
_EFFECT_SEVERITY = [
    "NO_SERVICE",
    "REDUCED_SERVICE",
    "SIGNIFICANT_DELAYS",
    "DETOUR",
    "MODIFIED_SERVICE",
    "STOP_MOVED",
    "ADDITIONAL_SERVICE",
    "OTHER_EFFECT",
    "UNKNOWN_EFFECT",
]


def _transport_section(journey: dict[str, Any]) -> dict[str, Any]:
    """Return the public transport section of a journey (or {})."""
    sections = journey.get("sections", [])
    if not isinstance(sections, list):
        return {}
    for section in sections:
        if isinstance(section, dict) and section.get("type") == "public_transport":
            return section
    for section in sections:
        if isinstance(section, dict):
            return section
    return {}


def planned_departure(journey: dict[str, Any]) -> datetime | None:
    """Return the planned (base) departure datetime of a journey."""
    section = _transport_section(journey)
    for value in (
        section.get("base_departure_date_time"),
        section.get("departure_date_time"),
        journey.get("departure_date_time"),
    ):
        parsed = parse_datetime(value or "")
        if parsed:
            return parsed
    return None


def journey_key(journey: dict[str, Any]) -> tuple[str, str] | None:
    """Identify a train run: (train number, planned departure)."""
    num = get_train_num(journey)
    dep = planned_departure(journey)
    if not num or dep is None:
        return None
    return num, dep.strftime("%Y%m%dT%H%M")


# ---------------------------------------------------------------------------
# Disruptions
# ---------------------------------------------------------------------------


def _simplify_disruption(disruption: dict[str, Any]) -> dict[str, Any]:
    """Keep only the fields useful to the user."""
    severity = disruption.get("severity") or {}
    effect = severity.get("effect") if isinstance(severity, dict) else None

    message = ""
    for msg in disruption.get("messages") or []:
        if isinstance(msg, dict) and msg.get("text"):
            message = str(msg["text"]).strip()
            break

    cause = disruption.get("cause") or ""
    if not cause:
        # Navitia also exposes the cause per impacted stop.
        for obj in disruption.get("impacted_objects") or []:
            for stop in (obj or {}).get("impacted_stops") or []:
                if isinstance(stop, dict) and stop.get("cause"):
                    cause = stop["cause"]
                    break
            if cause:
                break

    # Stops removed from the train's route (partial cancellation).
    deleted_stops: list[str] = []
    for obj in disruption.get("impacted_objects") or []:
        for stop in (obj or {}).get("impacted_stops") or []:
            if not isinstance(stop, dict):
                continue
            if "deleted" not in (
                stop.get("stop_time_effect"),
                stop.get("departure_status"),
                stop.get("arrival_status"),
            ):
                continue
            point = stop.get("stop_point") or {}
            area = point.get("stop_area") or {}
            for value in (point.get("id"), area.get("id"), point.get("name")):
                if value:
                    deleted_stops.append(str(value))

    return {
        "id": disruption.get("id") or disruption.get("disruption_id") or "",
        "effect": effect or "UNKNOWN_EFFECT",
        "message": message or str(cause),
        "cause": str(cause),
        "deleted_stops": deleted_stops,
    }


def _disruption_trip_names(disruption: dict[str, Any]) -> set[str]:
    """Train numbers impacted by a disruption."""
    names: set[str] = set()
    for obj in disruption.get("impacted_objects") or []:
        pt_object = (obj or {}).get("pt_object") or {}
        if not isinstance(pt_object, dict):
            continue
        trip = pt_object.get("trip") or {}
        for value in (
            trip.get("name") if isinstance(trip, dict) else None,
            pt_object.get("name"),
        ):
            if value:
                names.add(str(value))
    return names


def _linked_disruption_ids(journey: dict[str, Any]) -> set[str]:
    """Disruption ids referenced by a journey's links."""
    ids: set[str] = set()
    containers: list[Any] = [journey]
    for section in journey.get("sections") or []:
        if isinstance(section, dict):
            containers.append(section)
            containers.append(section.get("display_informations") or {})
    for container in containers:
        if not isinstance(container, dict):
            continue
        for link in container.get("links") or []:
            if (
                isinstance(link, dict)
                and link.get("type") == "disruption"
                and link.get("id")
            ):
                ids.add(str(link["id"]))
    return ids


def attach_disruptions(
    journeys: list[dict[str, Any]], disruptions: list[dict[str, Any]]
) -> None:
    """Attach the disruptions affecting each journey (in place).

    A disruption affects a journey when the journey links to it, or when it
    impacts a trip carrying the same train number.
    """
    by_id: dict[str, dict[str, Any]] = {}
    by_train: dict[str, list[dict[str, Any]]] = {}
    for disruption in disruptions or []:
        if not isinstance(disruption, dict):
            continue
        for key in (disruption.get("id"), disruption.get("disruption_id")):
            if key:
                by_id[str(key)] = disruption
        for name in _disruption_trip_names(disruption):
            by_train.setdefault(name, []).append(disruption)

    for journey in journeys:
        if not isinstance(journey, dict):
            continue
        found: dict[str, dict[str, Any]] = {}
        for dis_id in _linked_disruption_ids(journey):
            if dis_id in by_id:
                found[dis_id] = by_id[dis_id]
        num = get_train_num(journey)
        for disruption in by_train.get(num, []) if num else []:
            found[str(disruption.get("id") or id(disruption))] = disruption
        journey[KEY_DISRUPTIONS] = [_simplify_disruption(d) for d in found.values()]


def main_disruption(journey: dict[str, Any]) -> dict[str, Any] | None:
    """Most severe disruption attached to a journey, if any."""
    disruptions = journey.get(KEY_DISRUPTIONS) or []
    if not disruptions:
        return None

    def rank(dis: dict[str, Any]) -> int:
        effect = dis.get("effect")
        return (
            _EFFECT_SEVERITY.index(effect)
            if effect in _EFFECT_SEVERITY
            else len(_EFFECT_SEVERITY)
        )

    return min(disruptions, key=rank)


def _is_cancelled_by_disruption(journey: dict[str, Any]) -> bool:
    if str(journey.get("status", "")).upper() == EFFECT_NO_SERVICE:
        return True
    return any(
        d.get("effect") == EFFECT_NO_SERVICE for d in journey.get(KEY_DISRUPTIONS) or []
    )


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------


def merge_with_schedule(
    realtime: list[dict[str, Any]],
    base_schedule: list[dict[str, Any]] | None,
    arrival_id: str | None = None,
) -> list[dict[str, Any]]:
    """Merge realtime journeys with the planned timetable.

    Realtime journeys are kept as-is. A planned train missing from the
    realtime answer is added back, flagged as cancelled, when either:

    * a disruption explicitly says the train has no service, or
    * it was planned before the last realtime train (so it is not simply
      beyond the number of results requested).

    Exception: a planned train missing only because a disruption removed
    its stop at ``arrival_id`` is added back flagged "destination not
    served", not cancelled.

    The result is sorted by planned departure.
    """
    merged: list[dict[str, Any]] = []
    realtime_keys: set[tuple[str, str]] = set()
    latest_realtime: datetime | None = None

    for journey in realtime:
        if not isinstance(journey, dict):
            continue
        if _is_cancelled_by_disruption(journey):
            journey[KEY_STATUS] = STATUS_CANCELLED
        merged.append(journey)
        key = journey_key(journey)
        if key:
            realtime_keys.add(key)
        dep = planned_departure(journey)
        if dep and (latest_realtime is None or dep > latest_realtime):
            latest_realtime = dep

    for journey in base_schedule or []:
        if not isinstance(journey, dict):
            continue
        key = journey_key(journey)
        if key is None or key in realtime_keys:
            continue
        dep = planned_departure(journey)
        confirmed = _is_cancelled_by_disruption(journey)
        stop_deleted = destination_deleted(journey, arrival_id)
        within_range = (
            latest_realtime is not None and dep is not None and dep <= latest_realtime
        )
        if not (confirmed or stop_deleted or within_range):
            continue
        added = dict(journey)
        if stop_deleted and not confirmed:
            # The realtime planner drops it because it no longer reaches
            # the destination; the train itself still runs.
            added[KEY_DESTINATION_DELETED] = True
        else:
            added[KEY_STATUS] = STATUS_CANCELLED
        merged.append(added)
        realtime_keys.add(key)

    def sort_key(journey: dict[str, Any]) -> float:
        dep = planned_departure(journey)
        return dep.timestamp() if dep else float("inf")

    merged.sort(key=sort_key)
    return merged


def _stop_code(stop_id: str) -> str:
    """UIC code of a stop ("stop_area:SNCF:87611301" -> "87611301")."""
    codes = re.findall(r"\d{7,}", stop_id or "")
    return codes[0] if codes else (stop_id or "")


def destination_deleted(journey: dict[str, Any], arrival_id: str | None) -> bool:
    """True if a disruption removed the stop at the arrival station."""
    code = _stop_code(arrival_id or "")
    if not code:
        return False
    return any(
        code in stop
        for dis in journey.get(KEY_DISRUPTIONS) or []
        for stop in dis.get("deleted_stops") or []
    )


def mark_destination_deleted(journeys: list[dict[str, Any]], arrival_id: str) -> None:
    """Flag trains that no longer stop at the arrival station (in place).

    This is information only: the train still runs (it may stop before the
    destination), so it is not marked as cancelled.
    """
    for journey in journeys:
        if isinstance(journey, dict) and destination_deleted(journey, arrival_id):
            journey[KEY_DESTINATION_DELETED] = True


def has_departed(journey: dict[str, Any], now: datetime) -> bool:
    """True once the (realtime) departure time has passed."""
    dep = parse_datetime(journey.get("departure_date_time", "")) or planned_departure(
        journey
    )
    return dep is not None and dep <= now


def journey_status(
    journey: dict[str, Any],
    delay_minutes: int,
    now: datetime | None = None,
) -> str:
    """Status shown to the user.

    ``cancelled`` > ``departed`` > ``delayed`` > ``on_time``. Without
    ``now`` the departed state is not computed (service status only, used
    for change tracking so that departures do not produce events).
    """
    if journey.get(KEY_STATUS) == STATUS_CANCELLED:
        return STATUS_CANCELLED
    if now is not None and has_departed(journey, now):
        return STATUS_DEPARTED
    return STATUS_DELAYED if delay_minutes > 0 else STATUS_ON_TIME


def journey_cause(journey: dict[str, Any]) -> str | None:
    """Best available explanation for a disruption, if any.

    Order: most severe disruption message, cause on the train section,
    journey messages, then a generic text for a deleted destination stop.
    """
    disruption = main_disruption(journey) or {}
    if disruption.get("message"):
        return str(disruption["message"])

    section = _transport_section(journey)
    if section.get("cause"):
        return str(section["cause"]).strip()

    for msg in journey.get("messages") or []:
        if isinstance(msg, dict) and msg.get("text"):
            return str(msg["text"]).strip()

    if journey.get(KEY_DESTINATION_DELETED):
        name = (section.get("to") or {}).get("name") or "destination"
        return f"Arrêt {name} supprimé"

    return None
