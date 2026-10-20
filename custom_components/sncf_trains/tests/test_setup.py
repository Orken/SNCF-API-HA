"""Setting up the integration: one API refresh fills every entity."""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

from homeassistant import config_entries
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.sncf_trains.const import (
    CONF_API_KEY,
    CONF_ARRIVAL_NAME,
    CONF_DEPARTURE_NAME,
    CONF_FROM,
    CONF_TIME_START,
    CONF_TO,
    CONF_TRAIN_COUNT,
    DOMAIN,
)


def _journey(dep: datetime) -> dict:
    value = dep.strftime("%Y%m%dT%H%M%S")
    arrival = (dep + timedelta(minutes=40)).strftime("%Y%m%dT%H%M%S")
    return {
        "nb_transfers": 0,
        "departure_date_time": value,
        "arrival_date_time": arrival,
        "sections": [
            {
                "type": "public_transport",
                "id": f"section_{value}",
                "base_departure_date_time": value,
                "base_arrival_date_time": arrival,
                "display_informations": {"trip_short_name": value[-6:-2]},
            }
        ],
    }


async def _fetch(_from, _to, datetime_str, count=5, data_freshness="realtime"):
    """Trains 1 and 2 hours after the requested start (its day + 1)."""
    if data_freshness != "realtime":
        return []
    start = datetime.strptime(datetime_str, "%Y%m%dT%H%M%S") + timedelta(days=1)
    return [_journey(start + timedelta(hours=h)) for h in (1, 2)]


async def test_setup_refreshes_once_and_fills_every_entity(hass):
    entry = MockConfigEntry(
        version=1,
        minor_version=2,
        domain=DOMAIN,
        title="Trains SNCF",
        data={CONF_API_KEY: "key"},
        source=config_entries.SOURCE_USER,
        unique_id="sncf_trains",
        subentries_data=(
            {
                "data": {
                    CONF_FROM: "stop_area:dep",
                    CONF_TO: "stop_area:arr",
                    CONF_DEPARTURE_NAME: "Muret",
                    CONF_ARRIVAL_NAME: "Toulouse",
                    CONF_TIME_START: "07:00",
                    CONF_TRAIN_COUNT: 2,
                },
                "subentry_type": "train",
                "title": "Trajet: Muret → Toulouse (07:00, 2 trains)",
                "unique_id": "stop_area:dep_stop_area:arr_07:00_x2",
            },
        ),
    )
    entry.add_to_hass(hass)
    subentry_id = next(iter(entry.subentries))
    api = AsyncMock()
    api.fetch_journeys = AsyncMock(side_effect=_fetch)

    with patch(
        "custom_components.sncf_trains.coordinator.SncfApiClient",
        return_value=api,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        # Past the refresh debounce (10 s): no deferred refresh either.
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=11))
        await hass.async_block_till_done()

    realtime_calls = [
        call
        for call in api.fetch_journeys.await_args_list
        if call.kwargs.get("data_freshness", "realtime") == "realtime"
    ]
    assert len(realtime_calls) == 1

    registry = er.async_get(hass)
    line = registry.async_get_entity_id(
        "sensor", DOMAIN, f"{subentry_id}_all_trains_line"
    )
    calendar = registry.async_get_entity_id(
        "calendar", DOMAIN, f"calendar_sncf_train_{entry.entry_id}"
    )
    assert hass.states.get(line).state == "2"
    assert hass.states.get(calendar).attributes["message"] == "Muret → Toulouse"
