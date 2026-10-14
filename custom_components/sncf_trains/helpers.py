"""Helpers for component."""

import re
from datetime import datetime
from typing import Any

from homeassistant.util import dt as dt_util

from .const import CONF_TIME_END, CONF_TIME_START, CONF_TRAIN_COUNT, DEFAULT_TRAIN_COUNT


def parse_datetime(dt_str: str) -> datetime | None:
    """Parse string to datetime."""
    if not dt_str:
        return None

    try:
        dt = dt_util.parse_datetime(dt_str)
        return dt_util.as_local(dt) if dt else None
    except (ValueError, TypeError):
        return None


def format_time(dt_str: str) -> str:
    """Format a Navitia datetime string as dd/mm/YYYY - HH:MM."""
    dt = parse_datetime(dt_str)
    return dt.strftime("%d/%m/%Y - %H:%M") if dt else "N/A"


def get_train_num(journey: dict[str, Any]) -> str:
    """Extract the commercial train number from a journey."""
    # ---------------------------------------------------------
    # 1. Le journey peut directement contenir le numéro
    # ---------------------------------------------------------
    trip_num = journey.get("trip_short_name")

    if trip_num:
        return str(trip_num)

    # ---------------------------------------------------------
    # 2. Cherche dans les sections
    #
    # Les nouveaux trajets peuvent avoir par exemple :
    #
    # section[0] = crow_fly / walking
    # section[1] = public_transport
    # section[2] = crow_fly / walking
    #
    # Il ne faut donc plus supposer que sections[0] est le train.
    # ---------------------------------------------------------
    sections = journey.get("sections", [])

    if not isinstance(sections, list):
        return ""

    # ---------------------------------------------------------
    # 3. Priorité aux sections public_transport
    # ---------------------------------------------------------
    for section in sections:
        if not isinstance(section, dict):
            continue

        if section.get("type") != "public_transport":
            continue

        infos = section.get("display_informations", {})

        if not isinstance(infos, dict):
            continue

        trip_num = infos.get("trip_short_name")

        if trip_num:
            return str(trip_num)

        trip_num = infos.get("num")

        if trip_num:
            return str(trip_num)

    # ---------------------------------------------------------
    # 4. Fallback : chercher dans n'importe quelle section
    #
    # Utile pour conserver la compatibilité avec les anciens
    # formats de réponse API.
    # ---------------------------------------------------------
    for section in sections:
        if not isinstance(section, dict):
            continue

        infos = section.get("display_informations", {})

        if not isinstance(infos, dict):
            continue

        trip_num = infos.get("trip_short_name")

        if trip_num:
            return str(trip_num)

        trip_num = infos.get("num")

        if trip_num:
            return str(trip_num)

    # ---------------------------------------------------------
    # 5. Aucun numéro trouvé
    # ---------------------------------------------------------
    return ""


def get_duration(journey: dict[str, Any]) -> int:
    """Compute journey duration in minutes."""
    dep = parse_datetime(journey.get("departure_date_time", ""))

    arr = parse_datetime(journey.get("arrival_date_time", ""))

    if dep and arr:
        return int((arr - dep).total_seconds() / 60)

    return 0


def only_stations(places: Any) -> list[dict[str, Any]]:
    """Keep only stations (stop areas) from a Navitia places answer."""
    if not isinstance(places, list):
        return []
    return [
        place
        for place in places
        if isinstance(place, dict)
        and (
            place.get("embedded_type") == "stop_area"
            or str(place.get("id", "")).startswith("stop_area:")
        )
    ]


def short_station_name(name: str) -> str:
    """Drop a redundant city suffix: "L'Isle-Jourdain (L'Isle-Jourdain)" -> "L'Isle-Jourdain".

    A different city is kept: "Saint-Agne (Toulouse)" stays unchanged.
    """
    if not name:
        return name
    match = re.fullmatch(r"(.+?) \((.+)\)", name.strip())
    if match and match.group(1).strip().casefold() == match.group(2).strip().casefold():
        return match.group(1).strip()
    return name.strip()


def time_range_label(data: dict[str, Any]) -> str:
    """Plage d'un trajet : « 07:00 - 10:00 » ou « 07:00, 3 trains »."""
    time_start = data[CONF_TIME_START]
    if data.get(CONF_TIME_END):
        return f"{time_start} - {data[CONF_TIME_END]}"
    count = data.get(CONF_TRAIN_COUNT, DEFAULT_TRAIN_COUNT)
    return f"{time_start}, {count} train{'s' if count > 1 else ''}"


def time_range_id(data: dict[str, Any]) -> str:
    """Partie plage horaire de l'identifiant unique d'un trajet."""
    if data.get(CONF_TIME_END):
        return f"{data[CONF_TIME_START]}_{data[CONF_TIME_END]}"
    return f"{data[CONF_TIME_START]}_x{data.get(CONF_TRAIN_COUNT, DEFAULT_TRAIN_COUNT)}"


def trip_title(dep_name: str, arr_name: str, data: dict[str, Any]) -> str:
    """Titre d'un trajet."""
    return f"Trajet: {dep_name} → {arr_name} ({time_range_label(data)})"
