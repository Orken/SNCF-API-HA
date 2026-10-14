from typing import Any
import asyncio
from aiohttp import ClientError
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import SncfApiClient
from .const import (
    CONF_API_KEY,
    CONF_ARRIVAL_CITY,
    CONF_ARRIVAL_NAME,
    CONF_ARRIVAL_STATION,
    CONF_DEPARTURE_CITY,
    CONF_DEPARTURE_NAME,
    CONF_DEPARTURE_STATION,
    CONF_FROM,
    CONF_TIME_END,
    CONF_TIME_START,
    CONF_TO,
    CONF_TRAIN_COUNT,
    CONF_UPDATE_INTERVAL,
    CONF_OUTSIDE_INTERVAL,
    DEFAULT_OUTSIDE_INTERVAL,
    DEFAULT_TIME_START,
    DEFAULT_TRAIN_COUNT,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    MAX_TRAIN_COUNT,
)
from .helpers import time_range_id, trip_title


def _valid_time(value: str | None) -> bool:
    """True pour une heure au format HH:MM."""
    try:
        hours, minutes = map(int, value.split(":"))
    except (AttributeError, ValueError):
        return False
    return 0 <= hours < 24 and 0 <= minutes < 60


def _time_range_schema() -> vol.Schema:
    """Plage horaire : heure de fin facultative, nombre de trains requis."""
    return vol.Schema(
        {
            vol.Required(CONF_TIME_START, default=DEFAULT_TIME_START): str,
            vol.Optional(CONF_TIME_END): str,
            vol.Required(CONF_TRAIN_COUNT, default=DEFAULT_TRAIN_COUNT): vol.All(
                int, vol.Range(min=1, max=MAX_TRAIN_COUNT)
            ),
        }
    )


def _time_range_errors(user_input: dict[str, Any]) -> dict[str, str]:
    """Erreurs de saisie de la plage horaire."""
    errors = {}
    if not _valid_time(user_input.get(CONF_TIME_START)):
        errors[CONF_TIME_START] = "invalid_time"
    if user_input.get(CONF_TIME_END) and not _valid_time(user_input[CONF_TIME_END]):
        errors[CONF_TIME_END] = "invalid_time"
    return errors


def _clean_time_range(data: dict[str, Any]) -> dict[str, Any]:
    """Retire l'heure de fin quand elle est vide."""
    data = dict(data)
    if not data.get(CONF_TIME_END):
        data.pop(CONF_TIME_END, None)
    return data


class SncfTrainsConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow."""

    VERSION = 1
    MINOR_VERSION = 2

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Handle the initial step."""
        errors = {}
        if user_input is not None:
            session = async_get_clientsession(self.hass)
            api = SncfApiClient(session, user_input[CONF_API_KEY])
            if not await self._validate_api_key(api):
                errors["base"] = "invalid_api_key"
            else:
                if self.source == "user":
                    await self.async_set_unique_id("sncf_trains")
                    return self.async_create_entry(title="Trains SNCF", data=user_input)
                else:
                    return self.async_update_reload_and_abort(
                        self._get_reconfigure_entry(), data=user_input
                    )

        DATA_SCHEMA = vol.Schema({vol.Required(CONF_API_KEY): str})
        if self.source == "reconfigure":
            entry = self._get_reconfigure_entry()
            DATA_SCHEMA = self.add_suggested_values_to_schema(DATA_SCHEMA, entry.data)

        return self.async_show_form(
            step_id="user", data_schema=DATA_SCHEMA, errors=errors
        )

    async def _validate_api_key(self, api: SncfApiClient):
        """Check API Key."""
        try:
            results = await api.search_stations("paris")
            return bool(results)
        except (ClientError, asyncio.TimeoutError):
            return False

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Return subentries supported by this integration."""
        return {
            "train": TrainSubentryFlowHandler,
        }

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry):
        """Return options."""
        return SncfTrainsOptionsFlowHandler()

    async_step_reconfigure = async_step_user


class SncfTrainsOptionsFlowHandler(OptionsFlow):
    """Options flow."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        """Handle the initial options step."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        entry = self.config_entry

        DATA_SCHEMA = vol.Schema(
            {
                vol.Required(
                    CONF_UPDATE_INTERVAL,
                    default=entry.options.get(
                        CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL
                    ),
                ): int,
                vol.Required(
                    CONF_OUTSIDE_INTERVAL,
                    default=entry.options.get(
                        CONF_OUTSIDE_INTERVAL, DEFAULT_OUTSIDE_INTERVAL
                    ),
                ): int,
            }
        )

        return self.async_show_form(step_id="init", data_schema=DATA_SCHEMA)


class TrainSubentryFlowHandler(ConfigSubentryFlow):
    """Flow for managing trains subentries."""

    api: SncfApiClient | None = None
    departure_city: str | None = None
    departure_station: str | None = None
    arrival_city: str | None = None
    arrival_station: str | None = None
    departure_options: dict = {}
    arrival_options: dict = {}
    config_entry: ConfigEntry | None = None

    async def async_step_departure_city(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Handle the departure city step."""
        errors = {}
        if user_input is not None:
            self.config_entry = self._get_entry()
            api_key = self.config_entry.options.get(
                "api_key"
            ) or self.config_entry.data.get("api_key")
            session = async_get_clientsession(self.hass)
            self.api = SncfApiClient(session, api_key)

            self.departure_city = user_input[CONF_DEPARTURE_CITY]
            stations = await self.api.search_stations(self.departure_city)
            if not stations:
                errors["base"] = "no_stations"
            else:
                self.departure_options = {s["id"]: s for s in stations}
                return await self.async_step_departure_station()
        return self.async_show_form(
            step_id="departure_city",
            data_schema=vol.Schema({vol.Required(CONF_DEPARTURE_CITY): str}),
            errors=errors,
        )

    async def async_step_departure_station(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Handle the departure station step."""
        if user_input is not None:
            self.departure_station = user_input[CONF_DEPARTURE_STATION]
            return await self.async_step_arrival_city()
        options = {
            k: f"{v['name']} ({k.split(':')[-1]})"
            for k, v in self.departure_options.items()
        }
        return self.async_show_form(
            step_id="departure_station",
            data_schema=vol.Schema(
                {vol.Required(CONF_DEPARTURE_STATION): vol.In(options)}
            ),
        )

    async def async_step_arrival_city(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Handle the arrival city step."""
        errors = {}
        if user_input is not None:
            self.arrival_city = user_input[CONF_ARRIVAL_CITY]
            stations = await self.api.search_stations(self.arrival_city)
            if not stations:
                errors["base"] = "no_stations"
            else:
                self.arrival_options = {s["id"]: s for s in stations}
                return await self.async_step_arrival_station()
        return self.async_show_form(
            step_id="arrival_city",
            data_schema=vol.Schema({vol.Required(CONF_ARRIVAL_CITY): str}),
            errors=errors,
        )

    async def async_step_arrival_station(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Handle the arrival station step."""
        if user_input is not None:
            self.arrival_station = user_input[CONF_ARRIVAL_STATION]
            return await self.async_step_time_range()
        options = {
            k: f"{v['name']} ({k.split(':')[-1]})"
            for k, v in self.arrival_options.items()
        }
        return self.async_show_form(
            step_id="arrival_station",
            data_schema=vol.Schema(
                {vol.Required(CONF_ARRIVAL_STATION): vol.In(options)}
            ),
        )

    async def async_step_time_range(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Handle the time range step."""
        errors = {}
        if user_input is not None:
            errors = _time_range_errors(user_input)
        if user_input is not None and not errors:
            user_input = _clean_time_range(user_input)
            dep_name = self.departure_options.get(self.departure_station, {}).get(
                "name", self.departure_station
            )
            arr_name = self.arrival_options.get(self.arrival_station, {}).get(
                "name", self.arrival_station
            )
            unique_id = "_".join(
                (
                    self.departure_station,
                    self.arrival_station,
                    time_range_id(user_input),
                )
            )

            for subentry in self.config_entry.subentries.values():
                if unique_id == subentry.unique_id:
                    return self.async_abort(reason="already_configured_as_entry")

            return self.async_create_entry(
                title=trip_title(dep_name, arr_name, user_input),
                data={
                    CONF_FROM: self.departure_station,
                    CONF_TO: self.arrival_station,
                    CONF_DEPARTURE_NAME: dep_name,
                    CONF_ARRIVAL_NAME: arr_name,
                    **user_input,
                },
                unique_id=unique_id,
            )
        return self.async_show_form(
            step_id="time_range",
            data_schema=self.add_suggested_values_to_schema(
                _time_range_schema(), user_input or {}
            ),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """User flow to modify an existing entry."""
        config_subentry = self._get_reconfigure_subentry()

        errors = {}
        if user_input is not None:
            errors = _time_range_errors(user_input)
        if user_input is not None and not errors:
            data = config_subentry.data.copy()
            # Champ vidé : il est absent de user_input, on le retire.
            data.pop(CONF_TIME_END, None)
            data.update(user_input)
            data = _clean_time_range(data)

            return self.async_update_and_abort(
                self._get_entry(),
                config_subentry,
                data=data,
                title=trip_title(
                    data[CONF_DEPARTURE_NAME], data[CONF_ARRIVAL_NAME], data
                ),
            )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                _time_range_schema(),
                user_input if user_input is not None else config_subentry.data,
            ),
            errors=errors,
        )

    async_step_user = async_step_departure_city
